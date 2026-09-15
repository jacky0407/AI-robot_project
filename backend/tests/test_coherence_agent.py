"""
單元測試：跨步驟整合 Agent (core/ai/agents/coherence_agent.py)

測試策略：Mock LLM 呼叫，不消耗真實 API quota。
  - 測試 SSE 事件序列正確性
  - 測試一致性判斷邏輯
  - 測試步驟不足時的邊界條件
"""

import json
import pytest
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage

from core.ai.agents.coherence_agent import (
    CoherenceAgent, StepAnswer, DEFAULT_COHERENCE_CHECKS
)


def make_step(order: int, title: str = "", content: str = "作答內容", passed: bool = True):
    return StepAnswer(
        step_order=order,
        step_title=title or f"步驟{order}",
        content=content,
        passed=passed,
    )


def make_mock_llm(responses: list[dict]):
    """Mock LLM，依序回傳指定 JSON dict 的字串化結果。"""
    mock = AsyncMock()
    mock.ainvoke = AsyncMock(side_effect=[
        AIMessage(content=json.dumps(r, ensure_ascii=False))
        for r in responses
    ])
    return mock


# ── 測試 check_stream 串流事件序列 ─────────────────────────────────

class TestCoherenceStream:

    @pytest.mark.asyncio
    async def test_stream_starts_with_coherence_start(self):
        """第一個事件必須是 coherence_start"""
        # 只用 1 條規則的 agent，方便 mock
        agent = CoherenceAgent(checks=[DEFAULT_COHERENCE_CHECKS[0]])
        agent._llm = make_mock_llm([{"is_ok": True, "problem": "", "suggestion": ""}])

        steps = [make_step(1, content="優勢描述"), make_step(3, content="目標設定")]

        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        assert chunks[0]["type"] == "coherence_start"

    @pytest.mark.asyncio
    async def test_stream_ends_with_coherence_complete(self):
        """最後一個事件必須是 coherence_complete"""
        agent = CoherenceAgent(checks=[DEFAULT_COHERENCE_CHECKS[0]])
        agent._llm = make_mock_llm([{"is_ok": True, "problem": "", "suggestion": ""}])

        steps = [make_step(1), make_step(3)]
        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        assert chunks[-1]["type"] == "coherence_complete"

    @pytest.mark.asyncio
    async def test_yields_one_check_per_valid_rule(self):
        """每條有效規則都要 yield 一個 coherence_check 事件"""
        # 使用 3 條規則，但只提供步驟 1 和 3（第 2、3 條規則需要步驟 2 和 7）
        agent = CoherenceAgent(checks=DEFAULT_COHERENCE_CHECKS)
        agent._llm = make_mock_llm([
            {"is_ok": True, "problem": "", "suggestion": ""},   # 規則 1
        ])

        # 只提供步驟 1 和 3 → 只有第 1 條規則有效
        steps = [make_step(1), make_step(3)]
        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        check_events = [c for c in chunks if c["type"] == "coherence_check"]
        assert len(check_events) == 1

    @pytest.mark.asyncio
    async def test_overall_coherent_true_when_all_ok(self):
        """所有規則通過時，overall_coherent 應為 True"""
        agent = CoherenceAgent(checks=[DEFAULT_COHERENCE_CHECKS[0]])
        agent._llm = make_mock_llm([{"is_ok": True, "problem": "", "suggestion": ""}])

        steps = [make_step(1), make_step(3)]
        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        complete = next(c for c in chunks if c["type"] == "coherence_complete")
        assert complete["data"]["overall_coherent"] is True

    @pytest.mark.asyncio
    async def test_overall_coherent_false_when_any_fails(self):
        """有任一規則不通過時，overall_coherent 應為 False"""
        agent = CoherenceAgent(checks=[DEFAULT_COHERENCE_CHECKS[0]])
        agent._llm = make_mock_llm([{
            "is_ok": False,
            "problem": "步驟1的優勢描述與步驟3的目標完全無關",
            "suggestion": "請修改步驟3，將優勢納入目標設計"
        }])

        steps = [make_step(1), make_step(3)]
        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        complete = next(c for c in chunks if c["type"] == "coherence_complete")
        assert complete["data"]["overall_coherent"] is False
        assert len(complete["data"]["issues"]) == 1

    @pytest.mark.asyncio
    async def test_failed_check_includes_go_to_step(self):
        """不一致的規則結果必須包含 go_to_step_order（告訴學生去哪個步驟修改）"""
        agent = CoherenceAgent(checks=[DEFAULT_COHERENCE_CHECKS[0]])
        agent._llm = make_mock_llm([{
            "is_ok": False,
            "problem": "目標未呼應優勢",
            "suggestion": "修改步驟3"
        }])

        steps = [make_step(1), make_step(3)]
        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        check_event = next(c for c in chunks if c["type"] == "coherence_check")
        assert "go_to_step_order" in check_event["data"]
        assert check_event["data"]["go_to_step_order"] == 3  # target 步驟

    @pytest.mark.asyncio
    async def test_skips_rules_when_step_missing(self):
        """步驟資料不完整時，對應規則應被跳過，不報錯"""
        agent = CoherenceAgent(checks=DEFAULT_COHERENCE_CHECKS)  # 3 條規則
        # 只提供步驟 1，所有規則都需要 target 步驟 → 全部跳過
        agent._llm = make_mock_llm([])

        steps = [make_step(1)]  # 故意只提供 1 個步驟
        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        # 應該不 crash，且沒有 coherence_check 事件
        check_events = [c for c in chunks if c["type"] == "coherence_check"]
        assert len(check_events) == 0

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback(self):
        """LLM 回傳壞的 JSON 時，應用 fallback 值繼續，不讓整個 Agent crash"""
        agent = CoherenceAgent(checks=[DEFAULT_COHERENCE_CHECKS[0]])

        mock = AsyncMock()
        mock.ainvoke = AsyncMock(return_value=AIMessage(content="這不是 JSON"))
        agent._llm = mock

        steps = [make_step(1), make_step(3)]
        chunks = []
        async for chunk in agent.check_stream(steps):
            chunks.append(chunk)

        # 應有 coherence_complete（用 fallback 值）
        assert chunks[-1]["type"] == "coherence_complete"


# ── 測試 _build_summary ─────────────────────────────────────────────

class TestBuildSummary:

    def setup_method(self):
        self.agent = CoherenceAgent()

    def test_summary_positive_when_all_ok(self):
        """全部通過時，摘要應為正向鼓勵語句"""
        summary = self.agent._build_summary(overall_coherent=True, failed_issues=[])
        assert "恭喜" in summary or "一致" in summary

    def test_summary_mentions_steps_to_fix(self):
        """有失敗規則時，摘要應提到需要修改的步驟編號"""
        from core.ai.agents.coherence_agent import CoherenceIssue
        failed = [
            CoherenceIssue("規則A", False, "問題", "建議", go_to_step_order=3),
            CoherenceIssue("規則B", False, "問題", "建議", go_to_step_order=5),
        ]
        summary = self.agent._build_summary(overall_coherent=False, failed_issues=failed)
        assert "步驟 3" in summary
        assert "步驟 5" in summary
