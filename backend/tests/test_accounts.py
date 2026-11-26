"""
單元測試：帳號停用、去識別化與永久刪除（#87，core/accounts.py、api/accounts.py，migration 027）

用記憶體版 Supabase（tests/inmemory_db.py）＋假的 Supabase Auth 管理 API，不連線。

重點：
  - 停用／恢復：教授與助理都可以，助理只能對學生；會封鎖／解除 Supabase 登入
  - 去識別化：只限教授、只對學生；姓名與 Email 換成匿名研究編號，作答保留，原本的個資不留在紀錄裡
  - 永久刪除：只限教授、要輸入對方 Email、要 5 分鐘內重新驗證；先進 30 天回收期，可以復原
  - 清除到期帳號：只刪回收期已過的，失敗不影響其他
  - 不能對自己操作、最後一位教授不能被停用或刪除
  - 每個操作都有操作紀錄
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from api import accounts as accounts_api
from core import accounts
from core.auth import CurrentUser, get_current_user, require_recent_mfa
from tests.inmemory_db import InMemoryDB

PROF = "00000000-0000-4000-8000-0000000000a1"
PROF_2 = "00000000-0000-4000-8000-0000000000a2"
ASSIST = "00000000-0000-4000-8000-0000000000b1"
STU = "00000000-0000-4000-8000-0000000000c1"
STU_2 = "00000000-0000-4000-8000-0000000000c2"

PROFESSOR = CurrentUser(id=PROF, role="owner")
ASSISTANT = CurrentUser(id=ASSIST, role="assistant")


def profile(uid, role, name, email, status="active", **extra):
    return {"id": uid, "role": role, "full_name": name, "email": email, "is_active": status == "active",
            "account_status": status, "profession": "幼教老師", "created_at": "2026-11-01", **extra}


class FakeDB(InMemoryDB):
    """InMemoryDB 加上 Supabase Auth 管理 API（db.auth.admin.*）"""

    def __init__(self, **tables):
        super().__init__(**tables)
        self.auth = MagicMock()


def make_db(**overrides) -> FakeDB:
    tables = dict(
        profiles=[
            profile(PROF, "owner", "陳教授", "prof@x.com"),
            profile(ASSIST, "assistant", "王助理", "assist@x.com"),
            profile(STU, "student", "林小明", "ming@x.com"),
            profile(STU_2, "student", "張小華", "hua@x.com"),
        ],
        research_consents=[{"user_id": STU, "anonymous_research_id": "SPED-abc12345"}],
        step_attempts=[{"id": "att-1", "user_id": STU, "step_id": "s1", "user_input_content": "作答"}],
        user_api_keys=[{"user_id": STU, "encrypted_key": "x"}],
        audit_logs=[],
    )
    tables.update(overrides)
    return FakeDB(**tables)


def prof_row(db, uid):
    return next(p for p in db.rows("profiles") if p["id"] == uid)


@pytest.fixture(autouse=True)
def reset_login():
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def call(method, path, db, user=PROFESSOR, json=None):
    main.app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(accounts_api, "get_supabase", return_value=db):
        return client.request(method, f"/api/admin/accounts{path}", json=json)


def actions(db):
    return [l["action"] for l in db.rows("audit_logs")]


# ── 停用與恢復 ─────────────────────────────────────────────────────────────


class TestDisableEnable:

    def test_assistant_can_disable_student(self):
        db = make_db()
        res = call("POST", f"/{STU}/disable", db, user=ASSISTANT, json={"reason": "帳號疑似被盜"})
        assert res.status_code == 200
        row = prof_row(db, STU)
        assert (row["account_status"], row["is_active"]) == ("disabled", False)
        assert row["status_reason"] == "帳號疑似被盜" and row["status_changed_by"] == ASSIST
        db.auth.admin.update_user_by_id.assert_called_once_with(STU, {"ban_duration": accounts.BAN_FOREVER})
        assert actions(db) == ["account.disable"]

    def test_data_is_kept_when_disabled(self):
        db = make_db()
        call("POST", f"/{STU}/disable", db, json={"reason": "學期結束"})
        assert len(db.rows("step_attempts")) == 1
        assert prof_row(db, STU)["full_name"] == "林小明"

    def test_enable_restores_login(self):
        db = make_db(profiles=[profile(PROF, "owner", "陳教授", "p@x.com"),
                               profile(STU, "student", "林小明", "m@x.com", status="disabled")])
        res = call("POST", f"/{STU}/enable", db, user=ASSISTANT, json={})
        assert res.status_code == 200
        assert prof_row(db, STU)["is_active"] is True
        db.auth.admin.update_user_by_id.assert_called_once_with(STU, {"ban_duration": "none"})
        assert actions(db) == ["account.enable"]

    def test_assistant_cannot_disable_staff(self):
        db = make_db()
        res = call("POST", f"/{PROF}/disable", db, user=ASSISTANT, json={"reason": "測試"})
        assert res.status_code == 403
        assert prof_row(db, PROF)["is_active"] is True

    def test_owner_can_disable_assistant(self):
        db = make_db()
        assert call("POST", f"/{ASSIST}/disable", db, json={"reason": "離職"}).status_code == 200

    def test_cannot_disable_self(self):
        res = call("POST", f"/{PROF}/disable", make_db(), json={"reason": "測試"})
        assert res.json()["detail"]["code"] == "CANNOT_MANAGE_SELF"

    def test_last_active_owner_cannot_be_disabled(self):
        db = make_db()
        other_owner = CurrentUser(id=PROF_2, role="owner")
        db.tables["profiles"].append(profile(PROF_2, "owner", "另一位教授", "p2@x.com", status="disabled"))
        res = call("POST", f"/{PROF}/disable", db, user=other_owner, json={"reason": "測試"})
        assert res.status_code == 409 and res.json()["detail"]["code"] == "LAST_OWNER"

    def test_reason_required(self):
        res = call("POST", f"/{STU}/disable", make_db(), json={"reason": " "})
        assert res.status_code == 422

    def test_disable_twice_is_409(self):
        db = make_db()
        call("POST", f"/{STU}/disable", db, json={"reason": "第一次"})
        res = call("POST", f"/{STU}/disable", db, json={"reason": "第二次"})
        assert res.status_code == 409

    def test_auth_failure_changes_nothing(self):
        """Supabase 封鎖失敗 → 不能顯示「已停用」但其實還登得進去"""
        db = make_db()
        db.auth.admin.update_user_by_id.side_effect = RuntimeError("auth down")
        res = call("POST", f"/{STU}/disable", db, json={"reason": "測試"})
        assert res.status_code == 502
        assert prof_row(db, STU)["account_status"] == "active"

    def test_student_cannot_use_api(self):
        res = call("POST", f"/{STU_2}/disable", make_db(), user=CurrentUser(id=STU, role="student"), json={"reason": "x"})
        assert res.status_code == 403


# ── 去識別化 ───────────────────────────────────────────────────────────────


class TestDeidentify:

    def deidentify(self, db, user=PROFESSOR, confirm="去識別化"):
        return call("POST", f"/{STU}/deidentify", db, user=user, json={"reason": "學生畢業，資料留研究", "confirm": confirm})

    def test_replaces_identity_keeps_learning_history(self):
        db = make_db()
        res = self.deidentify(db)
        assert res.status_code == 200
        row = prof_row(db, STU)
        assert row["full_name"] == "SPED-abc12345"
        assert row["email"] == "sped-abc12345@deidentified.invalid"
        assert row["profession"] is None
        assert (row["account_status"], row["is_active"]) == ("deidentified", False)
        assert len(db.rows("step_attempts")) == 1           # 學習歷程保留
        assert db.rows("user_api_keys") == []                # 個人的金鑰刪掉

    def test_auth_email_also_replaced_and_banned(self):
        db = make_db()
        self.deidentify(db)
        args = db.auth.admin.update_user_by_id.call_args.args
        assert args[0] == STU
        assert args[1]["email"] == "sped-abc12345@deidentified.invalid"
        assert args[1]["ban_duration"] == accounts.BAN_FOREVER

    def test_audit_log_does_not_contain_original_identity(self):
        db = make_db()
        self.deidentify(db)
        log = db.rows("audit_logs")[0]
        assert log["action"] == "account.deidentify"
        assert "林小明" not in str(log) and "ming@x.com" not in str(log)

    def test_assistant_cannot_deidentify(self):
        db = make_db()
        assert self.deidentify(db, user=ASSISTANT).status_code == 403
        assert prof_row(db, STU)["full_name"] == "林小明"

    def test_wrong_confirm_text_is_400(self):
        db = make_db()
        assert self.deidentify(db, confirm="確定").json()["detail"]["code"] == "CONFIRM_MISMATCH"
        db.auth.admin.update_user_by_id.assert_not_called()

    def test_staff_cannot_be_deidentified(self):
        res = call("POST", f"/{ASSIST}/deidentify", make_db(), json={"reason": "測試", "confirm": "去識別化"})
        assert res.json()["detail"]["code"] == "STAFF_CANNOT_BE_DEIDENTIFIED"

    def test_cannot_deidentify_twice(self):
        db = make_db()
        self.deidentify(db)
        assert self.deidentify(db).status_code == 409

    def test_auth_failure_leaves_identity_untouched(self):
        db = make_db()
        db.auth.admin.update_user_by_id.side_effect = RuntimeError("auth down")
        assert self.deidentify(db).status_code == 502
        assert prof_row(db, STU)["full_name"] == "林小明"

    def test_generates_research_id_when_missing(self):
        db = make_db(research_consents=[])
        self.deidentify(db)
        assert prof_row(db, STU)["full_name"].startswith("SPED-")


# ── 永久刪除（30 天回收期）──────────────────────────────────────────────────


class TestDeleteAndRestore:

    def delete(self, db, email="ming@x.com", user=PROFESSOR, target=STU):
        return call("POST", f"/{target}/delete", db, user=user, json={"reason": "學生撤回同意，要求刪除", "confirm_email": email})

    def test_request_puts_account_in_30_day_recovery(self):
        db = make_db()
        res = self.delete(db)
        assert res.status_code == 200
        row = prof_row(db, STU)
        assert row["account_status"] == "pending_deletion" and row["is_active"] is False
        assert row["status_before_deletion"] == "active"
        scheduled = datetime.fromisoformat(row["deletion_scheduled_for"])
        assert timedelta(days=29) < scheduled - datetime.now(timezone.utc) <= timedelta(days=30)
        assert len(db.rows("step_attempts")) == 1           # 回收期內資料還在
        db.auth.admin.delete_user.assert_not_called()
        assert actions(db) == ["account.delete_request"]

    def test_email_confirmation_must_match(self):
        db = make_db()
        res = self.delete(db, email="someone@x.com")
        assert res.json()["detail"]["code"] == "CONFIRM_MISMATCH"
        assert prof_row(db, STU)["account_status"] == "active"

    def test_email_confirmation_ignores_case_and_spaces(self):
        assert self.delete(make_db(), email="  MING@x.com ").status_code == 200

    def test_assistant_cannot_delete(self):
        assert self.delete(make_db(), user=ASSISTANT).status_code == 403

    def test_requires_recent_mfa(self):
        """token 裡的驗證時間超過 5 分鐘 → 403 REAUTH_REQUIRED"""
        stale = CurrentUser(id=PROF, role="owner", aal="aal2", mfa_at=int(time.time()) - 600)
        res = self.delete(make_db(), user=stale)
        assert res.status_code == 403 and res.json()["detail"]["code"] == "REAUTH_REQUIRED"

    def test_recent_mfa_passes(self):
        fresh = CurrentUser(id=PROF, role="owner", aal="aal2", mfa_at=int(time.time()) - 30)
        assert self.delete(make_db(), user=fresh).status_code == 200

    def test_cannot_delete_last_owner(self):
        db = make_db()
        other = CurrentUser(id=PROF_2, role="owner")
        db.tables["profiles"].append(profile(PROF_2, "owner", "另一位", "p2@x.com", status="disabled"))
        res = self.delete(db, email="prof@x.com", user=other, target=PROF)
        assert res.json()["detail"]["code"] == "LAST_OWNER"

    def test_deidentified_account_can_be_deleted(self):
        db = make_db()
        call("POST", f"/{STU}/deidentify", db, json={"reason": "畢業", "confirm": "去識別化"})
        assert self.delete(db, email="sped-abc12345@deidentified.invalid").status_code == 200
        assert prof_row(db, STU)["status_before_deletion"] == "deidentified"

    @pytest.mark.parametrize("before,banned_after", [("active", False), ("disabled", True)])
    def test_restore_goes_back_to_previous_status(self, before, banned_after):
        db = make_db(profiles=[profile(PROF, "owner", "陳教授", "p@x.com"),
                               profile(STU, "student", "林小明", "ming@x.com", status=before)])
        self.delete(db)
        db.auth.admin.update_user_by_id.reset_mock()
        res = call("POST", f"/{STU}/restore", db)
        assert res.status_code == 200
        row = prof_row(db, STU)
        assert row["account_status"] == before and row["deletion_scheduled_for"] is None
        # 回到使用中才解除登入封鎖
        assert db.auth.admin.update_user_by_id.called is (not banned_after)
        assert actions(db)[-1] == "account.restore"

    def test_restore_only_from_pending(self):
        assert call("POST", f"/{STU}/restore", make_db()).status_code == 409


class TestPurge:

    def pending(self, uid, days_ago):
        scheduled = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return profile(uid, "student", "x", f"{uid}@x.com", status="pending_deletion",
                       deletion_scheduled_for=scheduled.isoformat(), status_reason="要求刪除")

    def test_only_due_accounts_are_deleted(self):
        db = make_db(profiles=[profile(PROF, "owner", "陳教授", "p@x.com"),
                               self.pending(STU, days_ago=1),        # 已到期
                               self.pending(STU_2, days_ago=-5)])    # 還有 5 天
        res = call("POST", "/purge-due", db)
        assert res.status_code == 200
        db.auth.admin.delete_user.assert_called_once_with(STU)
        assert res.json()["data"] == {"purged": [STU], "failed": []}
        log = db.rows("audit_logs")[0]
        assert (log["action"], log["target_id"], log["detail"]["deleted"]) == ("account.purge", STU, True)

    def test_one_failure_does_not_stop_others(self):
        db = make_db(profiles=[profile(PROF, "owner", "陳教授", "p@x.com"),
                               self.pending(STU, 1), self.pending(STU_2, 2)])
        db.auth.admin.delete_user.side_effect = [RuntimeError("fail"), None]
        res = call("POST", "/purge-due", db)
        assert res.json()["data"] == {"purged": [STU_2], "failed": [STU]}
        assert [l["detail"]["deleted"] for l in db.rows("audit_logs")] == [False, True]

    def test_assistant_cannot_purge(self):
        assert call("POST", "/purge-due", make_db(), user=ASSISTANT).status_code == 403

    def test_purge_requires_recent_mfa(self):
        stale = CurrentUser(id=PROF, role="owner", aal="aal2", mfa_at=int(time.time()) - 3600)
        assert call("POST", "/purge-due", make_db(), user=stale).json()["detail"]["code"] == "REAUTH_REQUIRED"


# ── 清單 ──────────────────────────────────────────────────────────────────


class TestList:

    def test_owner_sees_actions_per_account(self):
        res = call("GET", "", make_db())
        by_id = {a["id"]: a for a in res.json()["data"]}
        assert by_id[PROF]["actions"] == []                                     # 自己
        assert by_id[STU]["actions"] == ["disable", "deidentify", "delete"]
        assert by_id[ASSIST]["actions"] == ["disable", "delete"]                # 教師不能去識別化

    def test_assistant_only_gets_student_disable(self):
        res = call("GET", "", make_db(), user=ASSISTANT)
        by_id = {a["id"]: a for a in res.json()["data"]}
        assert by_id[STU]["actions"] == ["disable"]
        assert by_id[PROF]["actions"] == []

    def test_filter_and_search(self):
        db = make_db()
        call("POST", f"/{STU}/disable", db, json={"reason": "測試"})
        disabled = call("GET", "?status=disabled", db).json()["data"]
        found = call("GET", "?q=小華", db).json()["data"]
        assert [a["id"] for a in disabled] == [STU]
        assert disabled[0]["status_label"] == "已停用"
        assert [a["id"] for a in found] == [STU_2]

    def test_owner_sees_due_count(self):
        db = make_db()
        db.tables["profiles"].append(TestPurge().pending("00000000-0000-4000-8000-0000000000d1", 1))
        assert call("GET", "", db).json()["due_for_purge"] == 1

    def test_old_rows_without_account_status_use_is_active(self):
        assert accounts.status_of({"is_active": False}) == "disabled"
        assert accounts.status_of({"is_active": True}) == "active"


class TestRecentMfa:

    def test_within_five_minutes(self):
        user = CurrentUser(id="u", role="owner", aal="aal2", mfa_at=1000)
        require_recent_mfa(user, now=1000 + 299)

    def test_too_old(self):
        user = CurrentUser(id="u", role="owner", aal="aal2", mfa_at=1000)
        with pytest.raises(HTTPException) as e:
            require_recent_mfa(user, now=1000 + 301)
        assert e.value.detail["code"] == "REAUTH_REQUIRED"

    def test_no_mfa_in_token(self):
        with pytest.raises(HTTPException):
            require_recent_mfa(CurrentUser(id="u", role="owner", aal="aal2", mfa_at=None), now=1000)
