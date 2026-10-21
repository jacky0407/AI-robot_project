"""
單元測試：機器人申請 API (api/tool_requests.py)

用 MagicMock 取代 Supabase，不連線、不寫入。
「今天」固定為 2026-10-21（台灣時間），到期判斷才不會隨執行日期改變。
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from api import tool_requests
from core.auth import CurrentUser, get_current_user

TEACHER = CurrentUser(id="teacher-1", role="owner")
STUDENT = CurrentUser(id="student-1", role="student")

TODAY = date(2026, 10, 21)
TOOL_ID = "11111111-1111-1111-1111-111111111111"
REQ_ID = "22222222-2222-2222-2222-222222222222"

TOOL = {"id": TOOL_ID, "title": "功能性現況撰寫教練", "status": "published"}


def request_row(**overrides) -> dict:
    row = {
        "id": REQ_ID, "user_id": "student-1", "tool_id": TOOL_ID, "status": "pending",
        "daily_quota": 10, "start_date": "2026-10-20", "expire_date": None,
        "created_at": "2026-10-20T08:00:00Z",
    }
    row.update(overrides)
    return row


def fake_db(*results) -> MagicMock:
    """每次 .execute() 依序回傳 results 中的一個"""
    db = MagicMock()
    query = db.table.return_value
    for method in ("select", "insert", "update", "eq", "in_", "or_", "order", "limit"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [MagicMock(data=r) for r in results]
    return db


@pytest.fixture
def as_user():
    def _login(user: CurrentUser):
        main.app.dependency_overrides[get_current_user] = lambda: user
    yield _login
    main.app.dependency_overrides.clear()


def call(method: str, path: str, db: MagicMock, json=None):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(tool_requests, "get_supabase", return_value=db), \
         patch.object(tool_requests, "today_taipei", return_value=TODAY):
        return client.request(method, path, json=json)


def written(db: MagicMock, method: str) -> dict:
    return getattr(db.table.return_value, method).call_args.args[0]


# ──────────────────────────────────────────
# 到期判斷
# ──────────────────────────────────────────

class TestEffectiveStatus:

    @pytest.mark.parametrize("expire_date,expected", [
        (None, "approved"),            # 不限期
        ("2026-10-22", "approved"),
        ("2026-10-21", "approved"),    # 到期日當天仍可使用
        ("2026-10-20", "expired"),
    ])
    def test_approved_becomes_expired_after_expire_date(self, expire_date, expected):
        row = {"status": "approved", "expire_date": expire_date}
        assert tool_requests.effective_status(row, TODAY) == expected

    @pytest.mark.parametrize("status", ["pending", "rejected", "expired"])
    def test_other_statuses_are_unchanged(self, status):
        row = {"status": status, "expire_date": "2026-01-01"}
        assert tool_requests.effective_status(row, TODAY) == status

    def test_today_uses_taiwan_time(self):
        """UTC 10/20 17:00 已經是台灣 10/21 01:00"""
        from datetime import datetime, timezone
        utc = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
        with patch.object(tool_requests, "datetime") as fake_dt:
            fake_dt.now.side_effect = lambda tz: utc.astimezone(tz)
            assert tool_requests.today_taipei() == date(2026, 10, 21)


# ──────────────────────────────────────────
# 學生申請
# ──────────────────────────────────────────

class TestCreateRequest:

    def test_student_requests_published_tool(self, as_user):
        as_user(STUDENT)
        db = fake_db([TOOL], [], [request_row()])
        res = call("POST", "/api/tool-requests", db, json={"tool_id": TOOL_ID})

        assert res.status_code == 201
        data = res.json()["data"]
        assert data["status"] == "pending"
        assert data["tool_title"] == "功能性現況撰寫教練"
        assert written(db, "insert") == {"user_id": "student-1", "tool_id": TOOL_ID, "status": "pending"}

    def test_existing_check_includes_all_tools_permission(self, as_user):
        """tool_id 為 NULL 的核准代表全部機器人，也要算進去"""
        as_user(STUDENT)
        db = fake_db([TOOL], [], [request_row()])
        call("POST", "/api/tool-requests", db, json={"tool_id": TOOL_ID})

        db.table.return_value.or_.assert_called_once_with(f"tool_id.eq.{TOOL_ID},tool_id.is.null")

    @pytest.mark.parametrize("tool", [[], [{**TOOL, "status": "draft"}]])
    def test_missing_or_unpublished_tool_is_404(self, as_user, tool):
        as_user(STUDENT)
        db = fake_db(tool)
        res = call("POST", "/api/tool-requests", db, json={"tool_id": TOOL_ID})

        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "TOOL_NOT_FOUND"
        db.table.return_value.insert.assert_not_called()

    def test_pending_request_blocks_duplicate(self, as_user):
        as_user(STUDENT)
        db = fake_db([TOOL], [{"status": "pending", "expire_date": None}])
        res = call("POST", "/api/tool-requests", db, json={"tool_id": TOOL_ID})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ALREADY_PENDING"

    def test_valid_approval_blocks_duplicate(self, as_user):
        as_user(STUDENT)
        db = fake_db([TOOL], [{"status": "approved", "expire_date": "2026-11-06"}])
        res = call("POST", "/api/tool-requests", db, json={"tool_id": TOOL_ID})

        assert res.json()["detail"]["code"] == "ALREADY_APPROVED"

    def test_can_request_again_after_expiry(self, as_user):
        as_user(STUDENT)
        db = fake_db([TOOL], [{"status": "approved", "expire_date": "2026-10-01"}], [request_row()])
        res = call("POST", "/api/tool-requests", db, json={"tool_id": TOOL_ID})

        assert res.status_code == 201

    def test_non_uuid_tool_id_is_422_and_never_reaches_filter(self, as_user):
        """tool_id 會放進 .or_() 篩選字串，必須先擋掉非 UUID 的內容"""
        as_user(STUDENT)
        db = fake_db()
        res = call("POST", "/api/tool-requests", db, json={"tool_id": "x,user_id.neq.0"})

        assert res.status_code == 422
        db.table.assert_not_called()

    def test_not_logged_in_is_401(self):
        res = call("POST", "/api/tool-requests", fake_db(), json={"tool_id": TOOL_ID})
        assert res.status_code == 401


class TestMyRequests:

    def test_lists_own_requests_with_expired_status(self, as_user):
        as_user(STUDENT)
        db = fake_db(
            [request_row(status="approved", expire_date="2026-10-01")],
            [{"id": TOOL_ID, "title": "功能性現況撰寫教練"}],
        )
        res = call("GET", "/api/tool-requests/mine", db)

        item = res.json()["data"][0]
        assert item["status"] == "expired"
        assert item["tool_title"] == "功能性現況撰寫教練"
        db.table.return_value.eq.assert_called_once_with("user_id", "student-1")

    def test_all_tools_permission_has_readable_title(self, as_user):
        as_user(STUDENT)
        db = fake_db([request_row(tool_id=None, status="approved")])
        res = call("GET", "/api/tool-requests/mine", db)

        assert res.json()["data"][0]["tool_title"] == "全部機器人"

    def test_empty(self, as_user):
        as_user(STUDENT)
        res = call("GET", "/api/tool-requests/mine", fake_db([]))
        assert res.json() == {"data": []}


# ──────────────────────────────────────────
# 教師審核
# ──────────────────────────────────────────

class TestListRequests:

    def rows(self):
        profile = {"full_name": "王小明", "email": "s1@x.com"}
        return [
            {**request_row(id="r1", status="pending"), "profiles": profile},
            {**request_row(id="r2", status="approved", expire_date="2026-10-01"), "profiles": profile},
            {**request_row(id="r3", status="approved", expire_date="2026-12-31"), "profiles": profile},
        ]

    def test_pending_filter_includes_student_info(self, as_user):
        as_user(TEACHER)
        db = fake_db(self.rows(), [{"id": TOOL_ID, "title": "教練"}])
        res = call("GET", "/api/tool-requests?status=pending", db)

        data = res.json()["data"]
        assert [r["id"] for r in data] == ["r1"]
        assert data[0]["full_name"] == "王小明"
        assert data[0]["user_id"] == "student-1"

    def test_expired_filter_uses_computed_status(self, as_user):
        as_user(TEACHER)
        db = fake_db(self.rows(), [{"id": TOOL_ID, "title": "教練"}])
        res = call("GET", "/api/tool-requests?status=expired", db)

        assert [r["id"] for r in res.json()["data"]] == ["r2"]

    def test_invalid_status_filter_is_400(self, as_user):
        as_user(TEACHER)
        res = call("GET", "/api/tool-requests?status=whatever", fake_db())
        assert res.status_code == 400

    def test_student_cannot_list(self, as_user):
        as_user(STUDENT)
        res = call("GET", "/api/tool-requests", fake_db())
        assert res.status_code == 403


class TestReviewRequest:

    def test_approve_with_expiry_and_quota(self, as_user):
        as_user(TEACHER)
        db = fake_db(
            [request_row()],
            [request_row(status="approved", expire_date="2026-11-06", daily_quota=5)],
            [{"id": TOOL_ID, "title": "教練"}],
        )
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db,
                   json={"action": "approve", "expire_date": "2026-11-06", "daily_quota": 5})

        assert res.status_code == 200
        assert res.json()["data"]["status"] == "approved"
        assert written(db, "update") == {"status": "approved", "expire_date": "2026-11-06", "daily_quota": 5}

    def test_approve_without_settings_keeps_quota_and_expiry(self, as_user):
        """沒傳的欄位都不覆寫：每日次數沿用原值（預設 10）、到期日沿用原值"""
        as_user(TEACHER)
        db = fake_db([request_row()], [request_row(status="approved")], [{"id": TOOL_ID, "title": "教練"}])
        call("PATCH", f"/api/tool-requests/{REQ_ID}", db, json={"action": "approve"})

        assert written(db, "update") == {"status": "approved"}

    def test_changing_only_quota_keeps_expire_date(self, as_user):
        """
        回歸：舊版沒傳 expire_date 會寫成 NULL。
        權限到 11/30 的學生，老師只把每日次數改成 20，就變成永久可用。
        """
        as_user(TEACHER)
        db = fake_db(
            [request_row(status="approved", expire_date="2026-11-30")],
            [request_row(status="approved", expire_date="2026-11-30", daily_quota=20)],
            [{"id": TOOL_ID, "title": "教練"}],
        )
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db,
                   json={"action": "approve", "daily_quota": 20})

        assert res.status_code == 200
        assert written(db, "update") == {"status": "approved", "daily_quota": 20}

    def test_explicit_null_removes_expire_date(self, as_user):
        """要改成不限期，必須明確傳 null"""
        as_user(TEACHER)
        db = fake_db(
            [request_row(status="approved", expire_date="2026-11-30")],
            [request_row(status="approved")],
            [{"id": TOOL_ID, "title": "教練"}],
        )
        call("PATCH", f"/api/tool-requests/{REQ_ID}", db, json={"action": "approve", "expire_date": None})

        assert written(db, "update") == {"status": "approved", "expire_date": None}

    def test_reapproving_expired_without_new_date_is_400(self, as_user):
        """保留舊的到期日，核准完仍是過期；要求老師給新日期"""
        as_user(TEACHER)
        db = fake_db([request_row(status="approved", expire_date="2026-10-01")])
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db,
                   json={"action": "approve", "daily_quota": 20})

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "EXPIRE_DATE_REQUIRED"
        db.table.return_value.update.assert_not_called()

    def test_reapproving_expired_with_null_makes_it_unlimited(self, as_user):
        as_user(TEACHER)
        db = fake_db(
            [request_row(status="approved", expire_date="2026-10-01")],
            [request_row(status="approved")],
            [{"id": TOOL_ID, "title": "教練"}],
        )
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db, json={"action": "approve", "expire_date": None})

        assert res.json()["data"]["status"] == "approved"

    def test_approve_does_not_touch_start_date(self, as_user):
        """start_date 改成台灣的今天會比資料庫 CURRENT_DATE（UTC）多一天，學生半夜會暫時看不到機器人"""
        as_user(TEACHER)
        db = fake_db([request_row()], [request_row(status="approved")], [{"id": TOOL_ID, "title": "教練"}])
        call("PATCH", f"/api/tool-requests/{REQ_ID}", db, json={"action": "approve"})

        assert "start_date" not in written(db, "update")

    def test_approve_can_extend_expired_request(self, as_user):
        as_user(TEACHER)
        db = fake_db(
            [request_row(status="approved", expire_date="2026-10-01")],
            [request_row(status="approved", expire_date="2026-12-31")],
            [{"id": TOOL_ID, "title": "教練"}],
        )
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db,
                   json={"action": "approve", "expire_date": "2026-12-31"})

        assert res.json()["data"]["status"] == "approved"

    def test_expire_date_in_the_past_is_400(self, as_user):
        as_user(TEACHER)
        db = fake_db([request_row()])
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db,
                   json={"action": "approve", "expire_date": "2026-10-20"})

        assert res.json()["detail"]["code"] == "INVALID_EXPIRE_DATE"
        db.table.return_value.update.assert_not_called()

    @pytest.mark.parametrize("quota", [0, -1, 1001])
    def test_invalid_daily_quota_is_422(self, as_user, quota):
        as_user(TEACHER)
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", fake_db(),
                   json={"action": "approve", "daily_quota": quota})
        assert res.status_code == 422

    @pytest.mark.parametrize("before,message", [("pending", "已拒絕申請"), ("approved", "已撤銷使用權")])
    def test_reject_pending_or_revoke_approved(self, as_user, before, message):
        as_user(TEACHER)
        db = fake_db([request_row(status=before)], [request_row(status="rejected")],
                     [{"id": TOOL_ID, "title": "教練"}])
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db, json={"action": "reject"})

        assert res.json()["message"] == message
        assert written(db, "update") == {"status": "rejected"}

    @pytest.mark.parametrize("row", [
        request_row(status="rejected"),
        request_row(status="approved", expire_date="2026-10-01"),  # 已過期
    ])
    def test_cannot_reject_rejected_or_expired(self, as_user, row):
        as_user(TEACHER)
        db = fake_db([row])
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", db, json={"action": "reject"})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "INVALID_STATUS_TRANSITION"

    def test_unknown_request_is_404(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", fake_db([]), json={"action": "approve"})
        assert res.status_code == 404

    def test_non_uuid_request_id_is_422(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", "/api/tool-requests/not-a-uuid", fake_db(), json={"action": "approve"})
        assert res.status_code == 422

    def test_student_cannot_review(self, as_user):
        as_user(STUDENT)
        res = call("PATCH", f"/api/tool-requests/{REQ_ID}", fake_db(), json={"action": "approve"})
        assert res.status_code == 403
