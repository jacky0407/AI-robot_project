"""
單元測試：課程 API (api/courses.py)

用 MagicMock 取代 Supabase，不連線、不寫入。
登入者用 dependency_overrides 直接指定，不經過真正的 token 驗證（另見 test_auth.py）。
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import courses
from core.auth import CurrentUser, get_current_user

TEACHER = CurrentUser(id="teacher-1", role="owner")
STUDENT = CurrentUser(id="student-1", role="student")

COURSE = {"id": "c1", "title": "學前特教實習", "require_approval": False, "is_archived": False}


def db_error(code: str) -> APIError:
    return APIError({"code": code, "message": "db error", "details": "", "hint": None})


def fake_db(*results) -> MagicMock:
    """每次 .execute() 依序回傳 results 中的一個；Exception 會被拋出"""
    db = MagicMock()
    query = db.table.return_value
    for method in ("select", "insert", "update", "eq", "order", "limit"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [
        r if isinstance(r, Exception) else MagicMock(data=r) for r in results
    ]
    return db


@pytest.fixture
def as_user():
    def _login(user: CurrentUser):
        main.app.dependency_overrides[get_current_user] = lambda: user
    yield _login
    main.app.dependency_overrides.clear()


def call(method: str, path: str, db: MagicMock, json=None):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(courses, "get_supabase", return_value=db):
        return client.request(method, path, json=json)


def written(db: MagicMock, method: str) -> dict:
    """取出最後一次 insert / update 寫入的資料"""
    return getattr(db.table.return_value, method).call_args.args[0]


# ──────────────────────────────────────────
# 邀請碼
# ──────────────────────────────────────────

class TestInviteCode:

    def test_generated_code_avoids_confusing_characters(self):
        for _ in range(200):
            code = courses.generate_invite_code()
            assert len(code) == 8
            assert not set(code) & set("0O1IL")
            assert courses.INVITE_CODE_PATTERN.match(code)

    def test_option_b_style_custom_code_is_accepted(self):
        """之後改成做法 B，自訂代碼（含 -）也要能通過格式檢查"""
        assert courses.INVITE_CODE_PATTERN.match("IEP-2026-GRANT")


# ──────────────────────────────────────────
# 建立課程
# ──────────────────────────────────────────

class TestCreateCourse:

    def test_teacher_creates_course_with_invite_code(self, as_user):
        as_user(TEACHER)
        db = fake_db([{"id": "c1", "title": "學前特教實習", "invite_code": "K7MX4QPA"}])
        res = call("POST", "/api/courses", db, json={"title": "  學前特教實習 ", "require_approval": True})

        assert res.status_code == 201
        assert res.json()["data"]["invite_code"] == "K7MX4QPA"
        row = written(db, "insert")
        assert row["title"] == "學前特教實習"
        assert row["require_approval"] is True
        assert row["created_by"] == "teacher-1"
        assert len(row["invite_code"]) == 8

    def test_duplicate_invite_code_is_retried(self, as_user):
        as_user(TEACHER)
        db = fake_db(db_error("23505"), [{"id": "c1", "invite_code": "SECOND01"}])
        res = call("POST", "/api/courses", db, json={"title": "課程"})

        assert res.status_code == 201
        assert db.table.return_value.insert.call_count == 2

    def test_gives_up_after_repeated_collisions(self, as_user):
        as_user(TEACHER)
        db = fake_db(*[db_error("23505")] * courses.INVITE_CODE_MAX_RETRIES)
        res = call("POST", "/api/courses", db, json={"title": "課程"})

        assert res.status_code == 500
        assert res.json()["detail"]["code"] == "INVITE_CODE_GENERATION_FAILED"

    def test_blank_title_is_rejected(self, as_user):
        as_user(TEACHER)
        res = call("POST", "/api/courses", fake_db(), json={"title": "   "})
        assert res.status_code == 422

    def test_student_cannot_create_course(self, as_user):
        as_user(STUDENT)
        db = fake_db()
        res = call("POST", "/api/courses", db, json={"title": "課程"})

        assert res.status_code == 403
        db.table.assert_not_called()

    def test_not_logged_in_is_401(self):
        res = call("POST", "/api/courses", fake_db(), json={"title": "課程"})
        assert res.status_code == 401


# ──────────────────────────────────────────
# 課程列表與修改
# ──────────────────────────────────────────

class TestListAndUpdateCourses:

    def test_list_counts_active_and_pending_members(self, as_user):
        as_user(TEACHER)
        db = fake_db([{
            "id": "c1", "title": "課程",
            "course_members": [{"status": "active"}, {"status": "pending"},
                               {"status": "pending"}, {"status": "rejected"}],
        }])
        res = call("GET", "/api/courses", db)

        course = res.json()["data"][0]
        assert course["active_count"] == 1
        assert course["pending_count"] == 2
        assert "course_members" not in course

    def test_list_hides_archived_by_default(self, as_user):
        as_user(TEACHER)
        db = fake_db([])
        call("GET", "/api/courses", db)
        db.table.return_value.eq.assert_called_once_with("is_archived", False)

    def test_update_require_approval(self, as_user):
        as_user(TEACHER)
        db = fake_db([{"id": "c1", "require_approval": True}])
        res = call("PATCH", "/api/courses/c1", db, json={"require_approval": True})

        assert res.status_code == 200
        assert written(db, "update") == {"require_approval": True}

    def test_update_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", "/api/courses/nope", fake_db([]), json={"title": "新名稱"})
        assert res.status_code == 404

    def test_update_without_fields_is_400(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", "/api/courses/c1", fake_db(), json={})
        assert res.json()["detail"]["code"] == "NO_CHANGES"


# ──────────────────────────────────────────
# 用邀請碼加入
# ──────────────────────────────────────────

class TestJoinCourse:

    def test_open_course_joins_as_active(self, as_user):
        as_user(STUDENT)
        db = fake_db([COURSE], [], [{"status": "active", "joined_at": "2026-10-13T00:00:00Z"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": " k7mx4qpa "})

        assert res.status_code == 200
        body = res.json()["data"]
        assert body["course_id"] == "c1"
        assert body["course_name"] == "學前特教實習"
        assert body["status"] == "active"
        # 邀請碼會先轉大寫、去空白再查
        db.table.return_value.eq.assert_any_call("invite_code", "K7MX4QPA")
        assert written(db, "insert") == {"course_id": "c1", "user_id": "student-1", "status": "active"}

    def test_approval_course_joins_as_pending(self, as_user):
        as_user(STUDENT)
        db = fake_db([{**COURSE, "require_approval": True}], [], [{"status": "pending"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["data"]["status"] == "pending"
        assert written(db, "insert")["status"] == "pending"

    def test_unknown_code_is_404(self, as_user):
        as_user(STUDENT)
        res = call("POST", "/api/courses/join", fake_db([]), json={"invite_code": "NOPE1234"})

        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "INVALID_INVITE_CODE"

    @pytest.mark.parametrize("code", ["", "AB", "有中文的碼", "K7MX 4QPA"])
    def test_malformed_code_is_400(self, as_user, code):
        as_user(STUDENT)
        db = fake_db()
        res = call("POST", "/api/courses/join", db, json={"invite_code": code})

        assert res.status_code == 400
        db.table.assert_not_called()

    def test_archived_course_is_403(self, as_user):
        as_user(STUDENT)
        db = fake_db([{**COURSE, "is_archived": True}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["detail"]["code"] == "COURSE_ARCHIVED"

    @pytest.mark.parametrize("status,code", [("active", "ALREADY_JOINED"), ("pending", "ALREADY_PENDING")])
    def test_already_member_is_409(self, as_user, status, code):
        as_user(STUDENT)
        db = fake_db([COURSE], [{"id": "m1", "status": status}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == code

    @pytest.mark.parametrize("status", ["rejected", "removed"])
    def test_rejoin_after_rejected_or_removed_needs_approval_again(self, as_user, status):
        """被拒絕或移出的人重新輸入邀請碼，就算課程不需審核也要回到 pending"""
        as_user(STUDENT)
        db = fake_db([COURSE], [{"id": "m1", "status": status}], [{"status": "pending"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["data"]["status"] == "pending"
        assert written(db, "update") == {"status": "pending"}
        db.table.return_value.insert.assert_not_called()

    def test_double_submit_race_is_409_not_500(self, as_user):
        as_user(STUDENT)
        db = fake_db([COURSE], [], db_error("23505"))
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.status_code == 409

    def test_not_logged_in_is_401(self):
        res = call("POST", "/api/courses/join", fake_db(), json={"invite_code": "K7MX4QPA"})
        assert res.status_code == 401


class TestMyCourses:

    def test_lists_own_courses_with_status(self, as_user):
        as_user(STUDENT)
        db = fake_db([{"status": "pending", "joined_at": "t",
                       "courses": {"id": "c1", "title": "課程", "description": None, "is_archived": False}}])
        res = call("GET", "/api/courses/mine", db)

        assert res.json()["data"] == [{
            "course_id": "c1", "course_name": "課程", "description": None,
            "is_archived": False, "status": "pending", "joined_at": "t",
        }]
        db.table.return_value.eq.assert_called_once_with("user_id", "student-1")


# ──────────────────────────────────────────
# 成員與審核
# ──────────────────────────────────────────

class TestMembers:

    def test_pending_list_includes_name_and_email(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], [{"user_id": "s1", "status": "pending", "joined_at": "t",
                                 "profiles": {"full_name": "王小明", "email": "s1@x.com"}}])
        res = call("GET", "/api/courses/c1/members?status=pending", db)

        assert res.json()["data"] == [{
            "user_id": "s1", "full_name": "王小明", "email": "s1@x.com",
            "status": "pending", "joined_at": "t",
        }]

    def test_invalid_status_filter_is_400(self, as_user):
        as_user(TEACHER)
        res = call("GET", "/api/courses/c1/members?status=whatever", fake_db())
        assert res.status_code == 400

    def test_members_of_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("GET", "/api/courses/nope/members", fake_db([]))
        assert res.status_code == 404

    def test_student_cannot_see_members(self, as_user):
        as_user(STUDENT)
        res = call("GET", "/api/courses/c1/members", fake_db())
        assert res.status_code == 403

    @pytest.mark.parametrize("action,before,after", [
        ("approve", "pending", "active"),
        ("reject", "pending", "rejected"),
        ("remove", "active", "removed"),
        ("approve", "rejected", "active"),
    ])
    def test_member_actions(self, as_user, action, before, after):
        as_user(TEACHER)
        db = fake_db([{"id": "m1", "status": before}], [{"id": "m1", "status": after}])
        res = call("PATCH", "/api/courses/c1/members/s1", db, json={"action": action})

        assert res.status_code == 200
        assert written(db, "update") == {"status": after}

    def test_cannot_reject_active_member(self, as_user):
        """已加入的成員要用 remove，不是 reject，以免狀態意義混淆"""
        as_user(TEACHER)
        db = fake_db([{"id": "m1", "status": "active"}])
        res = call("PATCH", "/api/courses/c1/members/s1", db, json={"action": "reject"})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "INVALID_STATUS_TRANSITION"
        db.table.return_value.update.assert_not_called()

    def test_unknown_member_is_404(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", "/api/courses/c1/members/s1", fake_db([]), json={"action": "approve"})
        assert res.status_code == 404

    def test_unknown_action_is_422(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", "/api/courses/c1/members/s1", fake_db(), json={"action": "delete"})
        assert res.status_code == 422

    def test_student_cannot_approve(self, as_user):
        as_user(STUDENT)
        res = call("PATCH", "/api/courses/c1/members/s1", fake_db(), json={"action": "approve"})
        assert res.status_code == 403
