"""
單元測試：邀請碼刪除與過期自動清除（開會紀錄 #49 第 3 項）

  停用：不能再用它加入，已加入的人不受影響（既有行為，見 test_courses_api.py）
  刪除：從介面移除，並把用這組碼加入的學生移出課程；作答紀錄不動
  過期：過了可用日期視同刪除，讀取邀請碼清單、學生讀取自己的課程時自動清除
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from api import courses
from api.courses import delete_invite_code_and_members, purge_expired_invite_codes
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

TEACHER = CurrentUser(id="teacher-1", role="owner")
STUDENT = CurrentUser(id="s1", role="student")
C1 = "c0000000-0000-4000-8000-000000000001"
C2 = "c0000000-0000-4000-8000-000000000002"
CODE_A = "a0000000-0000-4000-8000-00000000000a"
CODE_B = "a0000000-0000-4000-8000-00000000000b"
CODE_OLD = "a0000000-0000-4000-8000-0000000000ff"
TODAY = date(2026, 11, 12)


def make_db():
    return InMemoryDB(
        courses=[{"id": C1, "title": "課程一", "is_archived": False, "require_approval": False},
                 {"id": C2, "title": "課程二", "is_archived": False, "require_approval": False}],
        course_invite_codes=[
            {"id": CODE_A, "course_id": C1, "code": "AAAA2222", "plan": "free", "daily_quota": 10,
             "access_until": "2027-01-31", "is_active": True, "created_at": "2026-11-01"},
            {"id": CODE_B, "course_id": C1, "code": "BBBB3333", "plan": "free", "daily_quota": 10,
             "access_until": "2027-01-31", "is_active": False, "created_at": "2026-11-02"},
            {"id": CODE_OLD, "course_id": C2, "code": "OLD99999", "plan": "free", "daily_quota": 10,
             "access_until": "2026-11-11", "is_active": True, "created_at": "2026-10-01"},
        ],
        course_members=[
            {"id": "m1", "course_id": C1, "user_id": "s1", "status": "active", "invite_code_id": CODE_A},
            {"id": "m2", "course_id": C1, "user_id": "s2", "status": "pending", "invite_code_id": CODE_A},
            {"id": "m3", "course_id": C1, "user_id": "s3", "status": "active", "invite_code_id": CODE_B},
            {"id": "m4", "course_id": C1, "user_id": "s4", "status": "rejected", "invite_code_id": CODE_A},
            {"id": "m5", "course_id": C2, "user_id": "s5", "status": "active", "invite_code_id": CODE_OLD},
        ],
        step_attempts=[{"id": "att-1", "user_id": "s1", "module_id": "m"}],
    )


def status_of(db, member_id):
    return next(m["status"] for m in db.rows("course_members") if m["id"] == member_id)


@pytest.fixture(autouse=True)
def as_teacher():
    main.app.dependency_overrides[get_current_user] = lambda: TEACHER
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def call(method, path, db):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(courses, "get_supabase", return_value=db), \
         patch.object(courses, "today_taipei", return_value=TODAY):
        return client.request(method, path)


class TestDeleteEndpoint:

    def test_delete_removes_code_and_its_members_only(self):
        db = make_db()
        res = call("DELETE", f"/api/courses/{C1}/invite-codes/{CODE_A}", db)

        assert res.status_code == 200
        body = res.json()
        assert body["removed_members"] == 2 and "2 位" in body["message"]
        assert CODE_A not in {c["id"] for c in db.rows("course_invite_codes")}
        assert status_of(db, "m1") == "removed" and status_of(db, "m2") == "removed"
        assert status_of(db, "m3") == "active"      # 別組碼加入的人不受影響
        assert status_of(db, "m4") == "rejected"    # 已拒絕的維持原狀
        assert len(db.rows("step_attempts")) == 1   # 作答紀錄不動

    def test_wrong_course_is_404_and_nothing_changes(self):
        db = make_db()
        res = call("DELETE", f"/api/courses/{C2}/invite-codes/{CODE_A}", db)

        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "INVITE_CODE_NOT_FOUND"
        assert status_of(db, "m1") == "active"

    def test_student_cannot_delete(self):
        main.app.dependency_overrides[get_current_user] = lambda: STUDENT
        db = make_db()
        assert call("DELETE", f"/api/courses/{C1}/invite-codes/{CODE_A}", db).status_code == 403
        assert len(db.rows("course_invite_codes")) == 3

    def test_disabled_code_can_also_be_deleted(self):
        db = make_db()
        res = call("DELETE", f"/api/courses/{C1}/invite-codes/{CODE_B}", db)
        assert res.json()["removed_members"] == 1 and status_of(db, "m3") == "removed"


class TestExpiry:

    def test_purge_only_touches_expired_codes(self):
        db = make_db()
        assert purge_expired_invite_codes(db, TODAY) == 1
        assert {c["id"] for c in db.rows("course_invite_codes")} == {CODE_A, CODE_B}
        assert status_of(db, "m5") == "removed" and status_of(db, "m1") == "active"

    def test_code_is_still_usable_on_its_last_day(self):
        """可用日期當天仍可用（與 core/access.py 一致），隔天才清除"""
        db = make_db()
        assert purge_expired_invite_codes(db, date(2026, 11, 11)) == 0

    def test_purge_can_be_limited_to_one_course(self):
        db = make_db()
        assert purge_expired_invite_codes(db, TODAY, C1) == 0
        assert len(db.rows("course_invite_codes")) == 3

    def test_listing_codes_purges_expired_ones_of_that_course(self):
        db = make_db()
        res = call("GET", f"/api/courses/{C2}/invite-codes", db)
        assert res.status_code == 200 and res.json()["data"] == []
        assert status_of(db, "m5") == "removed"

    def test_purge_failure_does_not_break_request(self):
        db = make_db()
        db.errors[("course_invite_codes", "select")] = RuntimeError("db down")
        assert purge_expired_invite_codes(db, TODAY) == 0

    def test_helper_returns_removed_count(self):
        db = make_db()
        assert delete_invite_code_and_members(db, CODE_OLD) == 1
