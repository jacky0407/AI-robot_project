"""
單元測試：步驟解鎖 (core/step_unlock.py)

重點：
  - 前面的必修步驟通過才解鎖；選修不擋；停用的步驟不算
  - 教師判定優先：已發布且有給分的複核決定通不通過，未發布的不算
  - 教師端不受限制
  - 還沒跑 migration 010（沒有 is_active）時規則照常
"""

import pytest
from fastapi import HTTPException

from core.auth import CurrentUser
from core.step_unlock import attempt_passed, check_step_unlocked, module_progress
from tests.inmemory_db import InMemoryDB

STUDENT = CurrentUser(id="student-1", role="student")
TEACHER = CurrentUser(id="teacher-1", role="owner")
MODULE = "module-1"


def step(n, *, required=True, active=True, pass_score=70):
    return {"id": f"s{n}", "module_id": MODULE, "step_order": n, "step_title": f"步驟{n}",
            "is_required": required, "is_active": active, "pass_score": pass_score}


def attempt(step_n, status="revision_required", reviews=None, user="student-1"):
    return {"user_id": user, "module_id": MODULE, "step_id": f"s{step_n}",
            "status": status, "teacher_reviews": reviews or []}


def review(score, published=True):
    return {"final_score": score, "is_published": published}


def progress(db, **kwargs):
    return {p["step_id"]: p for p in module_progress(db, "student-1", MODULE, **kwargs)}


class TestModuleProgress:

    def test_first_step_open_later_steps_locked_until_passed(self):
        db = InMemoryDB(module_steps=[step(1), step(2), step(3)])
        p = progress(db)

        assert p["s1"]["locked"] is False
        assert p["s2"]["locked"] is True and p["s2"]["locked_by"] == "步驟1"
        assert p["s3"]["locked"] is True and p["s3"]["locked_by"] == "步驟1"

    def test_passing_step_unlocks_next(self):
        db = InMemoryDB(module_steps=[step(1), step(2), step(3)], step_attempts=[attempt(1, "passed")])
        p = progress(db)

        assert p["s1"]["passed"] is True
        assert p["s2"]["locked"] is False
        assert p["s3"]["locked"] is True and p["s3"]["locked_by"] == "步驟2"

    def test_optional_step_does_not_block(self):
        db = InMemoryDB(module_steps=[step(1, required=False), step(2)])
        assert progress(db)["s2"]["locked"] is False

    def test_disabled_step_is_skipped_and_does_not_block(self):
        db = InMemoryDB(module_steps=[step(1, active=False), step(2)])
        p = progress(db)

        assert "s1" not in p
        assert p["s2"]["locked"] is False

    def test_other_students_attempts_do_not_count(self):
        db = InMemoryDB(module_steps=[step(1), step(2)], step_attempts=[attempt(1, "passed", user="someone")])
        assert progress(db)["s2"]["locked"] is True

    def test_staff_bypass_reports_no_locks(self):
        db = InMemoryDB(module_steps=[step(1), step(2)])
        p = progress(db, bypass_locks=True)

        assert p["s2"]["locked"] is False and p["s2"]["locked_by"] is None

    def test_works_before_migration_010(self):
        """沒有 is_active 欄位：第一次查詢失敗，改用不含 is_active 的查詢，全部視為啟用"""
        db = InMemoryDB(module_steps=[{k: v for k, v in step(n).items() if k != "is_active"} for n in (1, 2)])
        original = db.table

        def table(name):
            q = original(name)
            if name == "module_steps":
                real_execute = q.execute

                def execute():
                    if "is_active" in q.selected:
                        raise RuntimeError('column module_steps.is_active does not exist')
                    return real_execute()

                q.execute = execute
            return q

        db.table = table
        p = progress(db)
        assert p["s1"]["locked"] is False
        assert p["s2"]["locked"] is True


class TestAttemptPassed:

    def test_ai_status_used_without_teacher_review(self):
        assert attempt_passed({"status": "passed"}, 70) is True
        assert attempt_passed({"status": "revision_required"}, 70) is False

    def test_published_teacher_score_overrides_ai_pass(self):
        """AI 判過、老師給 50（門檻 70）→ 不算通過"""
        assert attempt_passed({"status": "passed", "teacher_reviews": [review(50)]}, 70) is False

    def test_published_teacher_score_overrides_ai_fail(self):
        assert attempt_passed({"status": "revision_required", "teacher_reviews": [review(80)]}, 70) is True

    def test_unpublished_review_is_ignored(self):
        assert attempt_passed({"status": "passed", "teacher_reviews": [review(10, published=False)]}, 70) is True

    def test_review_without_score_falls_back_to_status(self):
        assert attempt_passed({"status": "passed", "teacher_reviews": [review(None)]}, 70) is True

    def test_single_review_object_is_accepted(self):
        assert attempt_passed({"status": "revision_required", "teacher_reviews": review(90)}, 70) is True


class TestCheckStepUnlocked:

    def test_locked_step_raises_403_with_blocking_step_name(self):
        db = InMemoryDB(module_steps=[step(1), step(2)])
        with pytest.raises(HTTPException) as exc:
            check_step_unlocked(db, STUDENT, step(2))

        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "STEP_LOCKED"
        assert "步驟1" in exc.value.detail["message"]

    def test_unlocked_step_passes(self):
        db = InMemoryDB(module_steps=[step(1), step(2)], step_attempts=[attempt(1, "passed")])
        assert check_step_unlocked(db, STUDENT, step(2)) is None

    def test_disabled_step_raises_403_without_querying(self):
        db = InMemoryDB(module_steps=[step(1, active=False)])
        with pytest.raises(HTTPException) as exc:
            check_step_unlocked(db, STUDENT, step(1, active=False))

        assert exc.value.detail["code"] == "STEP_DISABLED"
        assert db.calls == []

    def test_staff_is_never_blocked(self):
        db = InMemoryDB(module_steps=[step(1), step(2, active=False)])
        assert check_step_unlocked(db, TEACHER, step(2, active=False)) is None
        assert db.calls == []
