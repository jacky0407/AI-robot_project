"""
單元測試：操作稽核紀錄（#90，core/audit.py、api/audit_logs.py，migration 026）

用記憶體版 Supabase（tests/inmemory_db.py），不連線、不寫入真的資料庫。
「只能新增、不能修改刪除」由資料庫觸發器保證（migration 026），這裡測後端：
  - 寫入的內容（誰、做了什麼、對象）、長字串截斷、寫入失敗不擋操作
  - 各教師端操作真的有留下紀錄
  - 查詢只限教授、篩選、分頁、操作者名稱
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from api import audit_logs, modules, teacher
from core import audit
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

PROFESSOR = CurrentUser(id="prof-1", role="owner")
ASSISTANT = CurrentUser(id="assistant-1", role="assistant")
STUDENT = CurrentUser(id="student-1", role="student")


@pytest.fixture(autouse=True)
def reset_login():
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def call(method, path, db, user=PROFESSOR, json=None, module=audit_logs):
    main.app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(module, "get_supabase", return_value=db):
        return client.request(method, path, json=json)


# ── 寫入 ──────────────────────────────────────────────────────────────────


class TestRecord:

    def test_writes_who_what_and_target(self):
        db = InMemoryDB()
        audit.record(db, ASSISTANT, "tool.update", target_type="tool", target_id="t1", detail={"version": 3})
        row = db.rows("audit_logs")[0]
        assert (row["actor_id"], row["actor_role"], row["action"]) == ("assistant-1", "assistant", "tool.update")
        assert (row["target_type"], row["target_id"], row["detail"]) == ("tool", "t1", {"version": 3})

    def test_long_text_is_truncated(self):
        """detail 不該放長文；不小心放了也會截斷，避免作答原文整段進紀錄"""
        db = InMemoryDB()
        audit.record(db, PROFESSOR, "tool.update", detail={"note": "字" * 1000, "nested": {"x": "y" * 500}})
        detail = db.rows("audit_logs")[0]["detail"]
        assert len(detail["note"]) == audit.MAX_DETAIL_TEXT + 1
        assert len(detail["nested"]["x"]) == audit.MAX_DETAIL_TEXT + 1

    def test_non_json_values_become_text(self):
        from uuid import UUID
        db = InMemoryDB()
        audit.record(db, PROFESSOR, "member.update", target_id=UUID(int=1), detail={"id": UUID(int=2)})
        row = db.rows("audit_logs")[0]
        assert row["target_id"] == str(UUID(int=1)) and row["detail"]["id"] == str(UUID(int=2))

    def test_failure_does_not_raise(self, caplog):
        db = InMemoryDB()
        db.errors[("audit_logs", "insert")] = RuntimeError("table missing")
        audit.record(db, PROFESSOR, "tool.delete", target_id="t1")  # 不拋例外
        assert "操作紀錄寫入失敗" in caplog.text
        assert "tool.delete" in caplog.text  # log 裡有完整內容，可以事後補


# ── 各操作真的有留下紀錄 ───────────────────────────────────────────────────


TOOL_BODY = {
    "title": "現況描述教練", "domain": "學前IEP", "target_competency": "現況描述",
    "role_instruction": "督導", "system_prompt": "引導學生", "rubric_criteria": [], "status": "draft",
}


class TestOperationsAreRecorded:

    def test_create_and_update_tool(self):
        db = InMemoryDB(ai_tools=[], ai_tool_versions=[], module_steps=[], step_attempts=[], chat_room_bots=[])
        tool_id = call("POST", "/api/teacher/tools", db, user=ASSISTANT, json=TOOL_BODY, module=teacher).json()["data"]["id"]
        call("PATCH", f"/api/teacher/tools/{tool_id}", db, user=ASSISTANT, module=teacher,
             json={"system_prompt": "新的指令", "rubric_criteria": [{"dimension": "A", "max_score": 10}]})

        logs = db.rows("audit_logs")
        assert [l["action"] for l in logs] == ["tool.create", "tool.update"]
        update = logs[1]["detail"]
        assert update["fields"] == ["rubric_criteria", "system_prompt"]
        assert update["rubric_changed"] is True
        assert (update["from_version"], update["version"]) == (1, 2)
        assert "新的指令" not in str(update)  # 不存內容本身（內容在 ai_tool_versions）

    def test_unchanged_tool_is_not_recorded(self):
        db = InMemoryDB(ai_tools=[{"id": "t1", **TOOL_BODY, "version": 1, "created_by": ASSISTANT.id}],
                        ai_tool_versions=[], module_steps=[], step_attempts=[], chat_room_bots=[])
        call("PATCH", "/api/teacher/tools/t1", db, user=ASSISTANT, module=teacher, json={"system_prompt": "引導學生"})
        assert db.rows("audit_logs") == []

    def test_denied_operation_is_not_recorded(self):
        db = InMemoryDB(ai_tools=[{"id": "t1", **TOOL_BODY, "version": 1, "created_by": "someone-else"}])
        res = call("PATCH", "/api/teacher/tools/t1", db, user=ASSISTANT, module=teacher, json={"system_prompt": "x"})
        assert res.status_code == 403
        assert db.rows("audit_logs") == []

    def test_module_publish_is_recorded(self):
        mod = "b0000000-0000-4000-8000-000000000001"
        tool = "a0000000-0000-4000-8000-00000000000a"
        db = InMemoryDB(
            learning_modules=[{"id": mod, "title": "模組", "is_published": False}],
            module_steps=[{"id": "s1", "module_id": mod, "tool_id": tool, "step_order": 1, "step_title": "一",
                           "is_active": True}],
            ai_tools=[{"id": tool, "title": "教練", "status": "published"}],
            step_attempts=[],
        )
        call("PATCH", f"/api/modules/{mod}", db, json={"is_published": True}, module=modules)
        row = db.rows("audit_logs")[0]
        assert (row["action"], row["target_id"], row["detail"]) == ("module.update", mod, {"is_published": True})


# ── 查詢 ──────────────────────────────────────────────────────────────────


def log(i, action="tool.update", actor="assistant-1", at="2026-11-20T02:00:00+00:00", **extra):
    return {"id": f"log-{i}", "actor_id": actor, "actor_role": "assistant", "action": action,
            "target_type": "tool", "target_id": "t1", "detail": {}, "created_at": at, **extra}


def query_db(logs, profiles=None):
    return InMemoryDB(
        audit_logs=logs,
        profiles=profiles if profiles is not None else [
            {"id": "assistant-1", "full_name": "王助理", "email": "a@x.com"},
            {"id": "prof-1", "full_name": "陳教授", "email": "p@x.com"},
        ],
    )


class TestQuery:

    def test_owner_sees_newest_first_with_names_and_labels(self):
        db = query_db([log(1, at="2026-11-01T00:00:00+00:00"), log(2, at="2026-11-02T00:00:00+00:00")])
        res = call("GET", "/api/admin/audit-logs", db)
        assert res.status_code == 200
        body = res.json()
        assert [r["id"] for r in body["data"]] == ["log-2", "log-1"]
        assert body["data"][0]["actor_name"] == "王助理"
        assert body["data"][0]["action_label"] == "修改機器人"
        assert {"value": "review.judge", "label": "教師判定"} in body["actions"]

    def test_assistant_cannot_query(self):
        res = call("GET", "/api/admin/audit-logs", query_db([]), user=ASSISTANT)
        assert res.status_code == 403

    def test_student_cannot_query(self):
        res = call("GET", "/api/admin/audit-logs", query_db([]), user=STUDENT)
        assert res.status_code == 403

    def test_filter_by_exact_action_and_prefix(self):
        db = query_db([log(1, "tool.update"), log(2, "tool.delete"), log(3, "review.judge")])
        exact = call("GET", "/api/admin/audit-logs?action=tool.update", db).json()["data"]
        prefix = call("GET", "/api/admin/audit-logs?action=tool.", db).json()["data"]
        assert [r["id"] for r in exact] == ["log-1"]
        assert {r["id"] for r in prefix} == {"log-1", "log-2"}

    def test_filter_by_actor(self):
        db = query_db([log(1, actor="assistant-1"), log(2, actor="prof-1")])
        res = call("GET", "/api/admin/audit-logs?actor_id=00000000-0000-0000-0000-000000000000", db)
        assert res.json()["data"] == []

    def test_date_range_uses_taiwan_days(self):
        """台灣 11/20 = UTC 11/19 16:00 ～ 11/20 16:00"""
        db = query_db([
            log(1, at="2026-11-19T15:59:00+00:00"),  # 台灣 11/19 23:59
            log(2, at="2026-11-19T16:00:00+00:00"),  # 台灣 11/20 00:00
            log(3, at="2026-11-20T15:59:00+00:00"),  # 台灣 11/20 23:59
            log(4, at="2026-11-20T16:00:00+00:00"),  # 台灣 11/21 00:00
        ])
        res = call("GET", "/api/admin/audit-logs?date_from=2026-11-20&date_to=2026-11-20", db)
        assert {r["id"] for r in res.json()["data"]} == {"log-2", "log-3"}

    def test_reversed_dates_is_400(self):
        res = call("GET", "/api/admin/audit-logs?date_from=2026-11-21&date_to=2026-11-20", query_db([]))
        assert res.status_code == 400

    def test_pagination_has_more(self):
        db = query_db([log(i, at=f"2026-11-{i:02d}T00:00:00+00:00") for i in range(1, 6)])
        first = call("GET", "/api/admin/audit-logs?limit=2", db).json()
        second = call("GET", "/api/admin/audit-logs?limit=2&offset=4", db).json()
        assert [r["id"] for r in first["data"]] == ["log-5", "log-4"] and first["has_more"] is True
        assert [r["id"] for r in second["data"]] == ["log-1"] and second["has_more"] is False

    def test_limit_over_200_is_422(self):
        res = call("GET", "/api/admin/audit-logs?limit=500", query_db([]))
        assert res.status_code == 422

    def test_deleted_account_has_placeholder_name(self):
        db = query_db([log(1, actor="gone")], profiles=[])
        assert call("GET", "/api/admin/audit-logs", db).json()["data"][0]["actor_name"] == "（已刪除的帳號）"

    def test_missing_migration_is_503(self):
        from postgrest.exceptions import APIError
        db = query_db([])
        db.errors[("audit_logs", "select")] = APIError({"code": "PGRST205", "message": "table not found"})
        res = call("GET", "/api/admin/audit-logs", db)
        assert res.status_code == 503
        assert "026_audit_logs.sql" in res.json()["detail"]["message"]
