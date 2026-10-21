"""
單元測試：作答權限與每日額度 (core/access.py)

用 FakeDB 取代 Supabase：每張表回傳固定資料，並記錄查詢條件與 rpc 呼叫。
篩選（status = active、approved 等）是資料庫做的，這裡檢查條件有正確送出。
"""

from datetime import date

import pytest
from fastapi import HTTPException

from core.access import StepAccess, check_step_access, consume_quota
from core.auth import CurrentUser

TODAY = date(2026, 10, 21)

STUDENT = CurrentUser(id="student-1", role="student")
TEACHER = CurrentUser(id="teacher-1", role="owner")

STEP = {"module_id": "module-1", "tool_id": "tool-1", "ai_tools": {"status": "published"}}


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table: str, db: "FakeDB"):
        self.table, self.db = table, db
        self.filters: list[tuple] = []

    def select(self, *args, **kwargs):
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, tuple(values)))
        return self

    def or_(self, expression):
        self.filters.append(("or", expression))
        return self

    def limit(self, *args):
        return self

    def execute(self):
        self.db.calls.append((self.table, self.filters))
        return FakeResult(self.db.tables.get(self.table, []))


class FakeRpc:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        self.db.rpc_calls.append((self.name, self.params))
        return FakeResult(self.db.rpc_result)


class FakeDB:
    def __init__(self, rpc_result=None, **tables):
        self.tables = tables
        self.rpc_result = rpc_result or []
        self.calls: list[tuple] = []
        self.rpc_calls: list[tuple] = []

    def table(self, name):
        return FakeQuery(name, self)

    def rpc(self, name, params):
        return FakeRpc(self, name, params)

    def queried(self, table) -> list[list]:
        return [filters for name, filters in self.calls if name == table]


PUBLISHED = [{"is_published": True}]


def assert_denied(exc: pytest.ExceptionInfo, status_code: int, code: str):
    assert exc.value.status_code == status_code
    assert exc.value.detail["code"] == code


# ──────────────────────────────────────────
# 指派檢查
# ──────────────────────────────────────────

class TestStepAccess:

    def test_staff_is_not_checked(self):
        db = FakeDB()
        assert check_step_access(db, TEACHER, STEP, TODAY) == StepAccess(mode="staff")
        assert db.calls == []

    def test_unpublished_tool_is_denied(self):
        db = FakeDB(learning_modules=PUBLISHED)
        step = {**STEP, "ai_tools": {"status": "draft"}}
        with pytest.raises(HTTPException) as exc:
            check_step_access(db, STUDENT, step, TODAY)
        assert_denied(exc, 403, "NOT_ASSIGNED")

    def test_unpublished_module_is_denied_even_if_assigned(self):
        db = FakeDB(
            learning_modules=[{"is_published": False}],
            course_modules=[{"course_id": "course-1"}],
            course_members=[{"course_id": "course-1", "courses": {"is_archived": False}}],
        )
        with pytest.raises(HTTPException) as exc:
            check_step_access(db, STUDENT, STEP, TODAY)
        assert_denied(exc, 403, "NOT_ASSIGNED")

    def test_active_member_of_assigned_course_is_allowed(self):
        db = FakeDB(
            learning_modules=PUBLISHED,
            course_modules=[{"course_id": "course-1"}],
            course_members=[{"course_id": "course-1", "courses": {"is_archived": False}}],
        )
        assert check_step_access(db, STUDENT, STEP, TODAY) == StepAccess(mode="course")

        member_filters = db.queried("course_members")[0]
        assert ("eq", "user_id", "student-1") in member_filters
        assert ("eq", "status", "active") in member_filters
        assert ("in", "course_id", ("course-1",)) in member_filters

    def test_archived_course_does_not_count(self):
        db = FakeDB(
            learning_modules=PUBLISHED,
            course_modules=[{"course_id": "course-1"}],
            course_members=[{"course_id": "course-1", "courses": {"is_archived": True}}],
        )
        with pytest.raises(HTTPException) as exc:
            check_step_access(db, STUDENT, STEP, TODAY)
        assert_denied(exc, 403, "NOT_ASSIGNED")

    def test_module_not_assigned_to_any_course_skips_member_query(self):
        db = FakeDB(learning_modules=PUBLISHED)
        with pytest.raises(HTTPException):
            check_step_access(db, STUDENT, STEP, TODAY)
        assert db.queried("course_members") == []

    def test_approved_permission_is_allowed(self):
        db = FakeDB(
            learning_modules=PUBLISHED,
            tool_permissions=[{"id": "perm-1", "tool_id": "tool-1",
                               "start_date": "2026-10-01", "expire_date": "2026-10-31"}],
        )
        access = check_step_access(db, STUDENT, STEP, TODAY)

        assert access == StepAccess(mode="permission", permission_id="perm-1")
        filters = db.queried("tool_permissions")[0]
        assert ("eq", "user_id", "student-1") in filters
        assert ("eq", "status", "approved") in filters
        assert ("or", "tool_id.eq.tool-1,tool_id.is.null") in filters

    def test_expire_date_today_is_still_valid(self):
        db = FakeDB(
            learning_modules=PUBLISHED,
            tool_permissions=[{"id": "perm-1", "tool_id": "tool-1",
                               "start_date": None, "expire_date": TODAY.isoformat()}],
        )
        assert check_step_access(db, STUDENT, STEP, TODAY).permission_id == "perm-1"

    def test_tool_specific_permission_is_preferred_over_all_tools(self):
        db = FakeDB(
            learning_modules=PUBLISHED,
            tool_permissions=[
                {"id": "perm-all", "tool_id": None, "start_date": None, "expire_date": None},
                {"id": "perm-tool", "tool_id": "tool-1", "start_date": None, "expire_date": None},
            ],
        )
        assert check_step_access(db, STUDENT, STEP, TODAY).permission_id == "perm-tool"

    def test_course_assignment_wins_over_permission(self):
        """課程指派的學生不扣額度，所以有課程指派時不要走到 permission"""
        db = FakeDB(
            learning_modules=PUBLISHED,
            course_modules=[{"course_id": "course-1"}],
            course_members=[{"course_id": "course-1", "courses": {"is_archived": False}}],
            tool_permissions=[{"id": "perm-1", "tool_id": "tool-1",
                               "start_date": None, "expire_date": None}],
        )
        assert check_step_access(db, STUDENT, STEP, TODAY).mode == "course"

    def test_expired_permission_has_its_own_error(self):
        db = FakeDB(
            learning_modules=PUBLISHED,
            tool_permissions=[{"id": "perm-1", "tool_id": "tool-1",
                               "start_date": "2026-09-01", "expire_date": "2026-10-20"}],
        )
        with pytest.raises(HTTPException) as exc:
            check_step_access(db, STUDENT, STEP, TODAY)
        assert_denied(exc, 403, "PERMISSION_EXPIRED")

    def test_permission_not_started_yet_is_denied(self):
        db = FakeDB(
            learning_modules=PUBLISHED,
            tool_permissions=[{"id": "perm-1", "tool_id": "tool-1",
                               "start_date": "2026-10-22", "expire_date": None}],
        )
        with pytest.raises(HTTPException) as exc:
            check_step_access(db, STUDENT, STEP, TODAY)
        assert_denied(exc, 403, "NOT_ASSIGNED")

    def test_no_assignment_at_all_is_denied(self):
        db = FakeDB(learning_modules=PUBLISHED)
        with pytest.raises(HTTPException) as exc:
            check_step_access(db, STUDENT, STEP, TODAY)
        assert_denied(exc, 403, "NOT_ASSIGNED")


# ──────────────────────────────────────────
# 每日額度
# ──────────────────────────────────────────

class TestConsumeQuota:

    @pytest.mark.parametrize("mode", ["staff", "course"])
    def test_staff_and_course_mode_are_not_charged(self, mode):
        db = FakeDB()
        assert consume_quota(db, StepAccess(mode=mode), TODAY) is None
        assert db.rpc_calls == []

    def test_permission_mode_consumes_one_with_taipei_date(self):
        db = FakeDB(rpc_result=[{"allowed": True, "used": 3, "quota": 10}])
        result = consume_quota(db, StepAccess(mode="permission", permission_id="perm-1"), TODAY)

        assert result == {"used": 3, "quota": 10}
        assert db.rpc_calls == [
            ("consume_tool_quota", {"p_permission_id": "perm-1", "p_today": "2026-10-21"})
        ]

    def test_quota_exhausted_returns_429(self):
        db = FakeDB(rpc_result=[{"allowed": False, "used": 10, "quota": 10}])
        with pytest.raises(HTTPException) as exc:
            consume_quota(db, StepAccess(mode="permission", permission_id="perm-1"), TODAY)

        assert_denied(exc, 429, "QUOTA_EXCEEDED")
        assert "10 次" in exc.value.detail["message"]

    def test_permission_deleted_after_check_is_denied(self):
        db = FakeDB(rpc_result=[])
        with pytest.raises(HTTPException) as exc:
            consume_quota(db, StepAccess(mode="permission", permission_id="perm-1"), TODAY)
        assert_denied(exc, 403, "NOT_ASSIGNED")
