"""
#65 評分規準改為可選、關卡通過方式（module_steps.pass_mode）

全部 Mock：不呼叫 Gemini、不連 Supabase。
"""

from unittest.mock import AsyncMock, patch

import pytest

from api import practice
from core.pass_mode import (
    effective_pass_mode,
    has_rubric,
    status_after_review,
    status_after_submit,
)
from tests import test_review_api as review_helpers
import main
from core.auth import get_current_user
from tests.test_practice_api import (  # noqa: F401 — 兩個 autouse fixture 要匯入才會生效
    assigned_via_course,
    logged_in_student,
    STEP_ROW,
    FakeSupabase,
    SYNTH_RESPONSE,
    dim_response,
    status_updates,
    submit,
)

NO_RUBRIC_STEP = {
    **STEP_ROW,
    "ai_tools": {**STEP_ROW["ai_tools"], "rubric_criteria": []},
}
FULL_SCORE_RESPONSES = [
    dim_response("客觀事實辨識", 4),
    dim_response("推論與假設區分", 4),
    dim_response("缺漏資訊提問", 4),
    SYNTH_RESPONSE,
]
LOW_SCORE_RESPONSES = [
    dim_response("客觀事實辨識", 1),
    dim_response("推論與假設區分", 1),
    dim_response("缺漏資訊提問", 1),
    SYNTH_RESPONSE,
]


# ──────────────────────────────────────────
# core/pass_mode.py
# ──────────────────────────────────────────

class TestRules:

    @pytest.mark.parametrize("criteria, expected", [
        ([{"dimension": "事實", "max_score": 10}], True),
        ([], False),
        (None, False),
        ([{"dimension": "  ", "max_score": 10}], False),  # 空白的評分項目不算
        ("not a list", False),
    ])
    def test_has_rubric(self, criteria, expected):
        assert has_rubric(criteria) is expected

    def test_missing_or_unknown_mode_falls_back_to_score(self):
        assert effective_pass_mode({}, True) == "score"
        assert effective_pass_mode({"pass_mode": "whatever"}, True) == "score"

    def test_score_mode_without_rubric_becomes_complete(self):
        """沒有分數可以比門檻，不能讓關卡永遠過不了"""
        assert effective_pass_mode({"pass_mode": "score"}, False) == "complete"

    def test_teacher_review_stays_without_rubric(self):
        assert effective_pass_mode({"pass_mode": "teacher_review"}, False) == "teacher_review"

    @pytest.mark.parametrize("mode, ai_passed, expected", [
        ("score", True, "passed"),
        ("score", False, "revision_required"),
        ("score", None, "revision_required"),    # 只給提示、沒評分
        ("complete", False, "passed"),           # 作答即通過：AI 判不過也算過
        ("complete", None, "passed"),
        ("teacher_review", True, None),          # 等老師，不改狀態
        ("teacher_review", None, None),
    ])
    def test_status_after_submit(self, mode, ai_passed, expected):
        assert status_after_submit(mode, ai_passed) == expected

    @pytest.mark.parametrize("mode, decision, score, expected", [
        ("teacher_review", "accept_ai", None, "passed"),
        ("teacher_review", "modify", 80, "passed"),
        ("teacher_review", "override", 50, "revision_required"),  # 有給分就比門檻
        ("teacher_review", "request_retry", None, "revision_required"),
        ("score", "request_retry", None, "revision_required"),
        ("score", "override", 90, None),   # score 關卡維持原做法：看已發布的教師分數
        ("complete", "accept_ai", None, None),
    ])
    def test_status_after_review(self, mode, decision, score, expected):
        assert status_after_review(mode, decision, score, 70) == expected


# ──────────────────────────────────────────
# 送出作答
# ──────────────────────────────────────────

def _submit_without_rubric(fake_db, feedback="很具體，下次補上缺漏資訊"):
    mock = AsyncMock(return_value=feedback)
    with patch.object(practice.feedback_agent, "feedback", mock):
        res, events = submit(fake_db, [])
    return res, events, mock


class TestSubmitWithoutRubric:

    def test_streams_feedback_and_never_writes_ai_evaluations(self):
        fake_db = FakeSupabase(NO_RUBRIC_STEP, prev_attempts=[])
        res, events, mock = _submit_without_rubric(fake_db)

        assert res.status_code == 200
        names = [e for e, _ in events]
        assert names == ["score_start", "feedback_complete", "pass_status"]
        start = dict(events)["score_start"]
        assert start["scoring"] is False
        assert start["pass_mode"] == "complete"      # 沒設 pass_mode、沒有規準 → 作答即通過
        assert dict(events)["feedback_complete"]["feedback"] == "很具體，下次補上缺漏資訊"
        assert fake_db.find("ai_evaluations", "insert") == []   # 不寫假分數
        mock.assert_awaited_once()

    def test_complete_mode_marks_attempt_passed_and_saves_feedback(self):
        fake_db = FakeSupabase(NO_RUBRIC_STEP, prev_attempts=[])
        _, events, _ = _submit_without_rubric(fake_db)

        assert status_updates(fake_db) == ["passed"]
        assert dict(events)["pass_status"]["passed"] is True
        saved = [c["payload"] for c in fake_db.find("step_attempts", "update") if "structured_data" in c["payload"]]
        assert saved == [{"structured_data": {"ai_feedback": "很具體，下次補上缺漏資訊"}}]

    def test_teacher_review_mode_waits_for_teacher(self):
        fake_db = FakeSupabase({**NO_RUBRIC_STEP, "pass_mode": "teacher_review"}, prev_attempts=[])
        _, events, _ = _submit_without_rubric(fake_db)

        assert status_updates(fake_db) == []          # 維持 submitted
        status = dict(events)["pass_status"]
        assert status["passed"] is False and status["awaiting_teacher"] is True

    def test_feedback_failure_reports_error(self):
        fake_db = FakeSupabase(NO_RUBRIC_STEP, prev_attempts=[])
        failing = AsyncMock(side_effect=RuntimeError("timeout"))
        with patch.object(practice.feedback_agent, "feedback", failing):
            res, events = submit(fake_db, [])

        assert res.status_code == 200
        assert [e for e, _ in events][-1] == "error"
        assert status_updates(fake_db) == []


class TestSubmitWithRubricAndPassMode:

    def test_complete_mode_passes_even_when_ai_score_is_low(self):
        fake_db = FakeSupabase({**STEP_ROW, "pass_mode": "complete"}, prev_attempts=[])
        _, events = submit(fake_db, LOW_SCORE_RESPONSES)

        assert dict(events)["score_complete"]["passed"] is False   # AI 分數照實回報
        assert status_updates(fake_db) == ["passed"]
        assert fake_db.find("ai_evaluations", "insert")            # 有規準照樣評分

    def test_teacher_review_mode_does_not_pass_on_ai_score(self):
        fake_db = FakeSupabase({**STEP_ROW, "pass_mode": "teacher_review"}, prev_attempts=[])
        _, events = submit(fake_db, FULL_SCORE_RESPONSES)

        assert dict(events)["score_complete"]["passed"] is True
        assert status_updates(fake_db) == []
        assert dict(events)["pass_status"]["awaiting_teacher"] is True

    def test_score_mode_is_unchanged(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        _, events = submit(fake_db, FULL_SCORE_RESPONSES)

        assert status_updates(fake_db) == ["passed"]
        assert dict(events)["pass_status"] == {
            "pass_mode": "score", "status": "passed", "passed": True, "awaiting_teacher": False,
        }


# ──────────────────────────────────────────
# 教師複核
# ──────────────────────────────────────────

def _attempt_on(pass_mode, *, rubric=True, status="submitted", evaluation=None):
    row = review_helpers.attempt("a1", evaluation=evaluation, tutor_action=None, status=status)
    row["module_steps"] = {
        **row["module_steps"],
        "pass_mode": pass_mode,
        "pass_score": 70,
        "ai_tools": {"title": "教練", "rubric_criteria": [{"dimension": "事實"}] if rubric else []},
    }
    return row


class TestReview:

    @pytest.fixture(autouse=True)
    def logged_in_teacher(self, logged_in_student):
        main.app.dependency_overrides[get_current_user] = lambda: review_helpers.TEACHER
        yield

    def test_teacher_review_attempt_without_evaluation_is_pending(self):
        db = review_helpers.FakeSupabase({("step_attempts", "select"): [_attempt_on("teacher_review", rubric=False)]})
        res = review_helpers.get("/api/review/pending", db)

        item = res.json()["data"][0]
        assert item["review_reasons"] == ["awaiting_teacher_pass"]
        assert item["pass_mode"] == "teacher_review"

    def test_complete_attempt_without_evaluation_is_not_pending(self):
        db = review_helpers.FakeSupabase({("step_attempts", "select"): [_attempt_on("complete", rubric=False, status="passed")]})
        res = review_helpers.get("/api/review/pending", db)

        assert res.json()["data"] == []

    def test_judge_on_teacher_review_step_passes_attempt(self):
        db = review_helpers.FakeSupabase({
            ("step_attempts", "select"): [_attempt_on("teacher_review", rubric=False)],
            ("teacher_reviews", "insert"): [{"id": "r1"}],
        })
        res = review_helpers.post("/api/review/submissions/a1/judge", db, {"decision": "accept_ai", "final_score": None})

        assert res.status_code == 201
        assert db.find("step_attempts", "update")[0]["payload"] == {"status": "passed"}
        assert db.find("ai_evaluations", "update") == []

    def test_judge_on_score_step_keeps_old_behaviour(self):
        db = review_helpers.FakeSupabase({
            ("step_attempts", "select"): [_attempt_on("score")],
            ("teacher_reviews", "insert"): [{"id": "r1"}],
        })
        review_helpers.post("/api/review/submissions/a1/judge", db, {"decision": "override", "final_score": 90})

        assert db.find("step_attempts", "update") == []

    def test_detail_shows_feedback_for_unscored_attempt(self):
        row = _attempt_on("complete", rubric=False, status="passed")
        row["structured_data"] = {"ai_feedback": "回饋文字"}
        db = review_helpers.FakeSupabase({("step_attempts", "select"): [row], ("prompt_logs", "select"): []})
        res = review_helpers.get("/api/review/submissions/a1", db)

        data = res.json()["data"]
        assert data["ai_feedback"] == "回饋文字"
        assert data["step"]["scoring"] is False
        assert data["step"]["pass_mode"] == "complete"


# ──────────────────────────────────────────
# 模組管理：設定關卡的通過方式
# ──────────────────────────────────────────

class TestModuleSteps:

    @pytest.fixture(autouse=True)
    def logged_in_professor(self, logged_in_student):
        from tests.test_modules_api import PROFESSOR
        main.app.dependency_overrides[get_current_user] = lambda: PROFESSOR
        yield

    def test_steps_default_to_score(self):
        from tests.test_modules_api import MOD, call, make_db
        res = call("GET", f"/{MOD}", make_db())

        assert {s["pass_mode"] for s in res.json()["data"]["steps"]} == {"score"}

    def test_add_step_with_complete_mode(self):
        from tests.test_modules_api import MOD, TOOL_A, call, make_db
        db = make_db()
        res = call("POST", f"/{MOD}/steps", db, json={"tool_id": TOOL_A, "step_title": "自由對話", "pass_mode": "complete"})

        assert res.status_code == 201
        assert res.json()["data"]["steps"][-1]["pass_mode"] == "complete"

    def test_update_step_to_teacher_review(self):
        from tests.test_modules_api import MOD, S1, call, make_db
        res = call("PATCH", f"/{MOD}/steps/{S1}", make_db(), json={"pass_mode": "teacher_review"})

        assert res.status_code == 200
        step = next(s for s in res.json()["data"]["steps"] if s["id"] == S1)
        assert step["pass_mode"] == "teacher_review"

    def test_unknown_pass_mode_is_rejected(self):
        from tests.test_modules_api import MOD, S1, call, make_db
        res = call("PATCH", f"/{MOD}/steps/{S1}", make_db(), json={"pass_mode": "auto"})

        assert res.status_code == 422

    def test_add_step_before_migration_019_returns_503(self):
        from postgrest.exceptions import APIError
        from tests.test_modules_api import MOD, TOOL_A, call, make_db
        db = make_db()
        db.errors[("module_steps", "insert")] = APIError(
            {"code": "42703", "message": "column module_steps.pass_mode does not exist", "details": "", "hint": None}
        )
        res = call("POST", f"/{MOD}/steps", db, json={"tool_id": TOOL_A, "step_title": "x", "pass_mode": "complete"})

        assert res.status_code == 503
        assert "019_step_pass_mode.sql" in res.json()["detail"]["message"]
