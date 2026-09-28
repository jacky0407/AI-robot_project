"""
單元測試：評分 Agent (core/ai/agents/evaluator_agent.py)

測試策略：Mock LLM 呼叫，不消耗真實 API quota。
  - 測試逐構面評分流程
  - 測試 SSE 串流事件順序
  - 測試 JSON 解析失敗時的容錯
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage

from core.ai.agents.evaluator_agent import EvaluatorAgent

SAMPLE_RUBRIC = {
    "pass_threshold_percent": 75,
    "dimensions": [
        {
            "name": "功能性描述",
            "weight": 40,
            "levels": [
                {"score": 4, "description": "完整描述，有具體行為觀察"},
                {"score": 3, "description": "大致完整，缺乏細節"},
                {"score": 2, "description": "描述籠統"},
                {"score": 1, "description": "未描述"},
            ],
        },
        {
            "name": "去標籤化用語",
            "weight": 20,
            "levels": [
                {"score": 4, "description": "全程使用優勢本位語言"},
                {"score": 1, "description": "大量標籤化用語"},
            ],
        },
    ],
}

SAMPLE_ANSWER = "小明在自由遊戲時段能主動與同伴互動，展現良好的輪流等待能力。"


def make_mock_llm(responses: list[str]):
    """建立一個 Mock LLM，依序回傳指定的字串回應。"""
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(side_effect=[
        AIMessage(content=r) for r in responses
    ])
    return mock


class TestEvaluatorAgentStream:

    @pytest.mark.asyncio
    async def test_stream_yields_analysis_start_first(self):
        """串流第一個事件必須是 analysis_start"""
        agent = EvaluatorAgent()

        dim_response = json.dumps({
            "dimension": "功能性描述", "score": 3, "max_score": 4,
            "reason": "描述尚可", "evidence": "主動與同伴互動"
        })
        synth_response = json.dumps({
            "overall_feedback": "整體良好。", "confidence": 0.85
        })
        agent._llm = make_mock_llm([dim_response, dim_response, synth_response])

        chunks = []
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "你是評分助理"):
            chunks.append(chunk)

        assert chunks[0]["type"] == "analysis_start"
        assert chunks[0]["data"]["total_dimensions"] == 2

    @pytest.mark.asyncio
    async def test_stream_yields_one_dimension_per_dimension(self):
        """每個構面評完後都要 yield 一次 dimension_score"""
        agent = EvaluatorAgent()

        dim_response = json.dumps({
            "dimension": "功能性描述", "score": 3, "max_score": 4,
            "reason": "不錯", "evidence": "主動互動"
        })
        synth_response = json.dumps({"overall_feedback": "良好", "confidence": 0.9})
        agent._llm = make_mock_llm([dim_response, dim_response, synth_response])

        chunks = []
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            chunks.append(chunk)

        dimension_events = [c for c in chunks if c["type"] == "dimension_score"]
        assert len(dimension_events) == 2  # rubric 有 2 個構面

    @pytest.mark.asyncio
    async def test_stream_last_event_is_score_complete(self):
        """串流最後一個事件必須是 score_complete"""
        agent = EvaluatorAgent()

        dim_response = json.dumps({
            "dimension": "功能性描述", "score": 4, "max_score": 4,
            "reason": "優秀", "evidence": "良好互動"
        })
        synth_response = json.dumps({"overall_feedback": "表現優異！", "confidence": 0.95})
        agent._llm = make_mock_llm([dim_response, dim_response, synth_response])

        chunks = []
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            chunks.append(chunk)

        assert chunks[-1]["type"] == "score_complete"

    @pytest.mark.asyncio
    async def test_score_complete_contains_required_fields(self):
        """score_complete 事件必須包含所有必要欄位"""
        agent = EvaluatorAgent()

        dim_response = json.dumps({
            "dimension": "功能性描述", "score": 3, "max_score": 4,
            "reason": "不錯", "evidence": "主動互動"
        })
        synth_response = json.dumps({"overall_feedback": "繼續努力", "confidence": 0.8})
        agent._llm = make_mock_llm([dim_response, dim_response, synth_response])

        score_complete = None
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            if chunk["type"] == "score_complete":
                score_complete = chunk["data"]

        assert score_complete is not None
        for field in ["total_score", "max_total_score", "percentage", "passed",
                      "overall_feedback", "confidence", "needs_teacher_review"]:
            assert field in score_complete, f"缺少欄位：{field}"

    @pytest.mark.asyncio
    async def test_passed_true_when_above_threshold(self):
        """分數超過門檻時，passed 應為 True"""
        agent = EvaluatorAgent()

        # 兩個構面都拿滿分 4/4 → 100% > 75%
        dim_response = json.dumps({
            "dimension": "功能性描述", "score": 4, "max_score": 4,
            "reason": "完美", "evidence": "..."
        })
        synth_response = json.dumps({"overall_feedback": "完美！", "confidence": 0.99})
        agent._llm = make_mock_llm([dim_response, dim_response, synth_response])

        score_complete = None
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            if chunk["type"] == "score_complete":
                score_complete = chunk["data"]

        assert score_complete["passed"] is True
        assert score_complete["percentage"] == 100.0

    @pytest.mark.asyncio
    async def test_json_parse_failure_uses_fallback(self):
        """LLM 回傳非 JSON 時，應使用預設值而非 crash"""
        agent = EvaluatorAgent()

        # 第一個構面回傳壞的 JSON
        bad_response = "這不是 JSON"
        synth_response = json.dumps({"overall_feedback": "已完成評分", "confidence": 0.6})
        agent._llm = make_mock_llm([bad_response, bad_response, synth_response])

        # 不應拋出例外
        chunks = []
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            chunks.append(chunk)

        # 最後應該還是有 score_complete
        assert chunks[-1]["type"] == "score_complete"


# ==============================================================================
# 加權計分（修正 KNOWN_GAPS #5）
# ==============================================================================

from core.ai.agents.evaluator_agent import compute_weighted_percentage


class TestComputeWeightedPercentage:

    def test_weights_are_applied(self):
        """
        80 分配分的構面拿滿分、20 分配分的構面拿 0（1/4 起跳這裡直接給 0），
        結果應接近 80%，而不是兩構面的簡單平均 50%。
        """
        scores = [
            {"score": 4, "max_score": 4, "weight": 80},
            {"score": 0, "max_score": 4, "weight": 20},
        ]
        assert compute_weighted_percentage(scores) == pytest.approx(80.0)

    def test_full_marks_is_one_hundred(self):
        scores = [
            {"score": 4, "max_score": 4, "weight": 40},
            {"score": 4, "max_score": 4, "weight": 60},
        ]
        assert compute_weighted_percentage(scores) == pytest.approx(100.0)

    def test_half_marks_is_fifty(self):
        scores = [
            {"score": 2, "max_score": 4, "weight": 30},
            {"score": 2, "max_score": 4, "weight": 70},
        ]
        assert compute_weighted_percentage(scores) == pytest.approx(50.0)

    def test_unequal_weights_differ_from_simple_average(self):
        """權重不同的情況，結果必須和未加權平均不一樣"""
        scores = [
            {"score": 4, "max_score": 4, "weight": 90},
            {"score": 1, "max_score": 4, "weight": 10},
        ]
        weighted = compute_weighted_percentage(scores)
        simple = (4 / 4 + 1 / 4) / 2 * 100
        assert weighted == pytest.approx(92.5)
        assert weighted != pytest.approx(simple)

    def test_no_weight_falls_back_to_raw_ratio(self):
        """沒有 weight 時（GeminiProvider 舊路徑）退回總分比例"""
        scores = [
            {"score": 3, "max_score": 4},
            {"score": 1, "max_score": 4},
        ]
        assert compute_weighted_percentage(scores) == pytest.approx(50.0)

    def test_empty_scores_returns_zero(self):
        assert compute_weighted_percentage([]) == 0.0

    def test_missing_max_score_uses_default_scale(self):
        scores = [{"score": 2, "weight": 100}]
        assert compute_weighted_percentage(scores) == pytest.approx(50.0)


class TestWeightedScoreComplete:

    @pytest.mark.asyncio
    async def test_score_complete_uses_weighted_total(self):
        """
        SAMPLE_RUBRIC 的權重是 40 / 20。
        第一構面 4/4、第二構面 1/4 →
        (1.0*40 + 0.25*20) / 60 * 100 = 75.0
        """
        agent = EvaluatorAgent()
        high = json.dumps({
            "dimension": "功能性描述", "score": 4, "max_score": 4,
            "reason": "完整", "evidence": "主動互動",
        })
        low = json.dumps({
            "dimension": "去標籤化用語", "score": 1, "max_score": 4,
            "reason": "仍有標籤化用語", "evidence": "…",
        })
        synth = json.dumps({"overall_feedback": "再加油", "confidence": 0.8})
        agent._llm = make_mock_llm([high, low, synth])

        score_complete = None
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            if chunk["type"] == "score_complete":
                score_complete = chunk["data"]

        assert score_complete["percentage"] == pytest.approx(75.0)
        assert score_complete["max_total_score"] == 100.0
        assert score_complete["passed"] is True      # 門檻 75

    @pytest.mark.asyncio
    async def test_dimension_score_carries_weight_from_rubric(self):
        """權重由 Rubric 帶入，不採信 AI 自己回傳的值"""
        agent = EvaluatorAgent()
        forged = json.dumps({
            "dimension": "功能性描述", "score": 3, "max_score": 4,
            "weight": 999,                     # AI 亂填，應被覆蓋
            "reason": "尚可", "evidence": "…",
        })
        synth = json.dumps({"overall_feedback": "良好", "confidence": 0.9})
        agent._llm = make_mock_llm([forged, forged, synth])

        dimension_events = []
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            if chunk["type"] == "dimension_score":
                dimension_events.append(chunk["data"])

        assert [d["weight"] for d in dimension_events] == [40, 20]

    @pytest.mark.asyncio
    async def test_fallback_dimension_still_has_weight(self):
        """JSON 解析失敗的降級結果也要帶權重，否則加權計算會少一項"""
        agent = EvaluatorAgent()
        synth = json.dumps({"overall_feedback": "已完成", "confidence": 0.6})
        agent._llm = make_mock_llm(["壞掉的回應", "壞掉的回應", synth])

        dimension_events = []
        async for chunk in agent.evaluate_stream(SAMPLE_ANSWER, SAMPLE_RUBRIC, "prompt"):
            if chunk["type"] == "dimension_score":
                dimension_events.append(chunk["data"])

        assert [d["weight"] for d in dimension_events] == [40, 20]
        assert all(d["score"] == 1 for d in dimension_events)
