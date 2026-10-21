"""
單元測試：指派 API (api/course_modules.py)

用 MagicMock 取代 Supabase，不連線、不寫入。
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import course_modules
from core.auth import CurrentUser, get_current_user

TEACHER = CurrentUser(id="teacher-1", role="owner")
STUDENT = CurrentUser(id="student-1", role="student")

COURSE_ID = "c0000000-0000-0000-0000-000000000001"
MODULE_ID = "b0000000-0000-0000-0000-000000000001"
BASE = f"/api/courses/{COURSE_ID}/modules"

COURSE = {"id": COURSE_ID, "title": "學前特教實習"}
MODULE = {"title": "IEP 撰寫", "description": None, "domain": "學前IEP", "is_published": True}


def assignment(module_id=MODULE_ID, **module_overrides) -> dict:
    return {"module_id": module_id, "assigned_at": "2026-10-21T00:00:00Z",
            "learning_modules": {**MODULE, **module_overrides}}


def db_error(code: str) -> APIError:
    return APIError({"code": code, "message": "db error", "details": "", "hint": None})


def fake_db(*results) -> MagicMock:
    """每次 .execute() 依序回傳 results 中的一個；Exception 會被拋出"""
    db = MagicMock()
    query = db.table.return_value
    for method in ("select", "insert", "delete", "eq", "order", "limit"):
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
    with patch.object(course_modules, "get_supabase", return_value=db):
        return client.request(method, path, json=json)


class TestAssignModule:

    def test_teacher_assigns_module(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], [MODULE], [{"module_id": MODULE_ID, "assigned_at": "t"}])
        res = call("POST", BASE, db, json={"module_id": MODULE_ID})

        assert res.status_code == 201
        assert res.json()["data"]["title"] == "IEP 撰寫"
        assert db.table.return_value.insert.call_args.args[0] == {
            "course_id": COURSE_ID, "module_id": MODULE_ID, "assigned_by": "teacher-1",
        }

    def test_unpublished_module_can_be_assigned_with_notice(self, as_user):
        """老師可以先排課；訊息提醒學生暫時看不到"""
        as_user(TEACHER)
        db = fake_db([COURSE], [{**MODULE, "is_published": False}], [{"module_id": MODULE_ID}])
        res = call("POST", BASE, db, json={"module_id": MODULE_ID})

        assert res.status_code == 201
        assert "尚未發布" in res.json()["message"]

    def test_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("POST", BASE, fake_db([]), json={"module_id": MODULE_ID})
        assert res.json()["detail"]["code"] == "COURSE_NOT_FOUND"

    def test_missing_module_is_404(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], [])
        res = call("POST", BASE, db, json={"module_id": MODULE_ID})

        assert res.json()["detail"]["code"] == "MODULE_NOT_FOUND"
        db.table.return_value.insert.assert_not_called()

    def test_duplicate_assignment_is_409(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], [MODULE], db_error("23505"))
        res = call("POST", BASE, db, json={"module_id": MODULE_ID})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "ALREADY_ASSIGNED"

    def test_non_uuid_module_id_is_422(self, as_user):
        as_user(TEACHER)
        res = call("POST", BASE, fake_db(), json={"module_id": "abc"})
        assert res.status_code == 422

    def test_student_cannot_assign(self, as_user):
        as_user(STUDENT)
        db = fake_db()
        res = call("POST", BASE, db, json={"module_id": MODULE_ID})

        assert res.status_code == 403
        db.table.assert_not_called()

    def test_not_logged_in_is_401(self):
        res = call("POST", BASE, fake_db(), json={"module_id": MODULE_ID})
        assert res.status_code == 401


class TestListCourseModules:

    def test_teacher_sees_all_including_unpublished(self, as_user):
        as_user(TEACHER)
        other = "b0000000-0000-0000-0000-000000000002"
        db = fake_db([COURSE], [assignment(), assignment(other, is_published=False)])
        res = call("GET", BASE, db)

        data = res.json()["data"]
        assert [m["module_id"] for m in data] == [MODULE_ID, other]
        assert data[1]["is_published"] is False

    def test_teacher_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("GET", BASE, fake_db([]))
        assert res.status_code == 404

    def test_active_member_sees_only_published(self, as_user):
        as_user(STUDENT)
        other = "b0000000-0000-0000-0000-000000000002"
        db = fake_db([{"status": "active"}], [assignment(), assignment(other, is_published=False)])
        res = call("GET", BASE, db)

        assert [m["module_id"] for m in res.json()["data"]] == [MODULE_ID]
        db.table.return_value.eq.assert_any_call("user_id", "student-1")

    @pytest.mark.parametrize("member", [[], [{"status": "pending"}], [{"status": "removed"}]])
    def test_non_member_or_pending_is_403(self, as_user, member):
        as_user(STUDENT)
        res = call("GET", BASE, fake_db(member))

        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "NOT_COURSE_MEMBER"

    def test_non_uuid_course_id_is_422(self, as_user):
        as_user(TEACHER)
        res = call("GET", "/api/courses/not-a-uuid/modules", fake_db())
        assert res.status_code == 422


class TestUnassignModule:

    def test_teacher_unassigns(self, as_user):
        as_user(TEACHER)
        db = fake_db([{"module_id": MODULE_ID}])
        res = call("DELETE", f"{BASE}/{MODULE_ID}", db)

        assert res.json()["success"] is True
        db.table.return_value.eq.assert_any_call("course_id", COURSE_ID)
        db.table.return_value.eq.assert_any_call("module_id", MODULE_ID)

    def test_missing_assignment_is_404(self, as_user):
        as_user(TEACHER)
        res = call("DELETE", f"{BASE}/{MODULE_ID}", fake_db([]))
        assert res.json()["detail"]["code"] == "ASSIGNMENT_NOT_FOUND"

    def test_student_cannot_unassign(self, as_user):
        as_user(STUDENT)
        res = call("DELETE", f"{BASE}/{MODULE_ID}", fake_db())
        assert res.status_code == 403
