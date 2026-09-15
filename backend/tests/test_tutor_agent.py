"""
單元測試：教學 Agent 決策邏輯 (core/ai/agents/tutor_agent.py)

TutorAgent.decide_action() 是純邏輯函式，不需要 Mock，直接測試。
TutorAgent.process_stream() 的串流測試使用 Mock EvaluatorAgent。
"""

import pytest
from unittest.mock import AsyncMock, patch

from core.ai.agents.tutor_agent import TutorAgent, TutorAction, StudentContext


def make_ctx(
    attempt=1,
    last_score=None,
    hints_used=0,
    threshold=75.0,
) -> StudentContext:
    return StudentContext(
        attempt_number=attempt,
        last_score_percent=last_score,
        hints_used_count=hints_used,
        pass_threshold=threshold,
    )


# ──────────────────────────────────────────
# 決策邏輯（純邏輯，不需要 Mock）
# ──────────────────────────────────────────

class TestTutorDecision:

    def setup_method(self):
        self.agent = TutorAgent()

    def test_first_attempt_always_evaluate(self):
        """第一次作答必定直接評分"""
        ctx = make_ctx(attempt=1, last_score=None)
        assert self.agent.decide_action(ctx) == TutorAction.EVALUATE

    def test_passing_score_always_evaluate(self):
        """上次已達通過門檻，繼續評分"""
        ctx = make_ctx(attempt=2, last_score=80.0, threshold=75.0)
        assert self.agent.decide_action(ctx) == TutorAction.EVALUATE

    def test_encourage_when_close_to_threshold(self):
        """分數接近門檻（差距 < 10%）且嘗試 <= 3 次，給鼓勵"""
        ctx = make_ctx(attempt=2, last_score=68.0, threshold=75.0)  # 差 7%
        assert self.agent.decide_action(ctx) == TutorAction.ENCOURAGE

    def test_give_hint_when_score_between_60_and_threshold_no_hints(self):
        """分數 60~75 且從未用過提示 → 給提示"""
        ctx = make_ctx(attempt=2, last_score=63.0, threshold=75.0, hints_used=0)
        assert self.agent.decide_action(ctx) == TutorAction.GIVE_HINT

    def test_evaluate_when_score_between_60_and_threshold_hints_used(self):
        """分數 60~75 但已用過提示 → 直接評分"""
        ctx = make_ctx(attempt=3, last_score=63.0, threshold=75.0, hints_used=1)
        assert self.agent.decide_action(ctx) == TutorAction.EVALUATE

    def test_give_hint_when_low_score_early_attempt(self):
        """分數低於 60%，嘗試次數 <= 3 → 給提示"""
        ctx = make_ctx(attempt=2, last_score=45.0, threshold=75.0)
        assert self.agent.decide_action(ctx) == TutorAction.GIVE_HINT

    def test_escalate_when_low_score_many_attempts(self):
        """分數低於 60%，嘗試次數 > 3 → 建議找老師"""
        ctx = make_ctx(attempt=4, last_score=40.0, threshold=75.0)
        assert self.agent.decide_action(ctx) == TutorAction.ESCALATE

    def test_boundary_exactly_at_threshold(self):
        """剛好等於門檻分數 → 評分（已通過）"""
        ctx = make_ctx(attempt=2, last_score=75.0, threshold=75.0)
        assert self.agent.decide_action(ctx) == TutorAction.EVALUATE

    def test_boundary_60_percent(self):
        """剛好 60% 且從未用提示 → 給提示（60 屬於「60 以上」的區間）"""
        ctx = make_ctx(attempt=2, last_score=60.0, threshold=75.0, hints_used=0)
        assert self.agent.decide_action(ctx) == TutorAction.GIVE_HINT


# ──────────────────────────────────────────
# 串流輸出測試（Mock EvaluatorAgent）
# ──────────────────────────────────────────

class TestTutorProcessStream:

    @pytest.mark.asyncio
    async def test_stream_starts_with_tutor_decision(self):
        """串流第一個事件必須是 tutor_decision"""
        agent = TutorAgent()
        ctx = make_ctx(attempt=1)  # 第一次 → EVALUATE

        mock_eval_chunks = [
            {"type": "analysis_start", "data": {"total_dimensions": 1}},
            {"type": "score_complete", "data": {
                "total_score": 3, "max_total_score": 4,
                "percentage": 75.0, "passed": True,
                "overall_feedback": "不錯", "confidence": 0.9,
                "needs_teacher_review": False, "dimension_scores": [],
            }},
        ]

        async def mock_evaluate_stream(**kwargs):
            for chunk in mock_eval_chunks:
                yield chunk

        with patch.object(agent._evaluator, "evaluate_stream", mock_evaluate_stream):
            chunks = []
            async for chunk in agent.process_stream(
                student_answer="作答內容",
                rubric={"pass_threshold_percent": 75, "dimensions": []},
                system_prompt="prompt",
                student_context=ctx,
            ):
                chunks.append(chunk)

        assert chunks[0]["type"] == "tutor_decision"
        assert chunks[0]["data"]["action"] == "evaluate"

    @pytest.mark.asyncio
    async def test_hint_stream_does_not_call_evaluator(self):
        """GIVE_HINT 行動時，不應呼叫 EvaluatorAgent"""
        agent = TutorAgent()
        ctx = make_ctx(attempt=2, last_score=45.0, hints_used=0)  # → GIVE_HINT

        eval_called = []

        async def mock_evaluate_stream(**kwargs):
            eval_called.append(True)
            yield {"type": "score_complete", "data": {}}

        with patch.object(agent._evaluator, "evaluate_stream", mock_evaluate_stream):
            chunks = []
            async for chunk in agent.process_stream(
                student_answer="作答", rubric={}, system_prompt="p", student_context=ctx
            ):
                chunks.append(chunk)

        assert not eval_called, "GIVE_HINT 時不應呼叫 EvaluatorAgent"
        hint_events = [c for c in chunks if c["type"] == "hint"]
        assert len(hint_events) == 1

    @pytest.mark.asyncio
    async def test_escalate_stream_contains_message(self):
        """ESCALATE 時，回傳的事件應包含提示訊息和 suggest_teacher_review"""
        agent = TutorAgent()
        ctx = make_ctx(attempt=4, last_score=30.0)  # → ESCALATE

        chunks = []
        async for chunk in agent.process_stream(
            student_answer="作答", rubric={}, system_prompt="p", student_context=ctx
        ):
            chunks.append(chunk)

        escalation = next(c for c in chunks if c["type"] == "escalation")
        assert "suggest_teacher_review" in escalation["data"]
        assert escalation["data"]["suggest_teacher_review"] is True
