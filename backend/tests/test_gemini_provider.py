"""
單元測試：Gemini AI Provider (core/ai/gemini_provider.py)

測試策略：Mock Gemini API，不消耗真實 API quota。
  - 測試 JSON 解析邏輯（_parse_response）
  - 測試信心值判斷（needs_teacher_review）
  - 測試格式錯誤時的處理
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.ai.gemini_provider import GeminiProvider


# ──────────────────────────────────────────
# 測試 _parse_response（不需要呼叫 API）
# ──────────────────────────────────────────

class TestParseResponse:

    def setup_method(self):
        self.provider = GeminiProvider()

    def test_parse_valid_response(self):
        raw = """{
            "dimension_scores": [
                {"name": "功能性描述", "score": 3, "max_score": 4, "reason": "不錯", "evidence": "小明能..."},
                {"name": "去標籤化", "score": 4, "max_score": 4, "reason": "優秀", "evidence": "使用優勢語言"}
            ],
            "overall_feedback": "整體表現良好，建議更具體說明行為觀察。",
            "confidence": 0.85
        }"""
        result = self.provider._parse_response(raw)

        assert result.total_score == 7
        assert result.max_total_score == 8
        assert result.percentage == 87.5
        assert result.overall_feedback == "整體表現良好，建議更具體說明行為觀察。"
        assert result.confidence == 0.85
        assert result.needs_teacher_review is False  # confidence >= 0.7

    def test_needs_teacher_review_when_low_confidence(self):
        """信心值低於 0.7 時，應標記需要教師複核"""
        raw = """{
            "dimension_scores": [
                {"name": "構面A", "score": 2, "max_score": 4, "reason": "不確定", "evidence": "..."}
            ],
            "overall_feedback": "尚待改善。",
            "confidence": 0.5
        }"""
        result = self.provider._parse_response(raw)
        assert result.needs_teacher_review is True

    def test_percentage_calculation(self):
        """百分比計算正確性"""
        raw = """{
            "dimension_scores": [
                {"name": "A", "score": 3, "max_score": 4, "reason": "", "evidence": ""},
                {"name": "B", "score": 1, "max_score": 4, "reason": "", "evidence": ""}
            ],
            "overall_feedback": "test",
            "confidence": 0.9
        }"""
        result = self.provider._parse_response(raw)
        assert result.total_score == 4
        assert result.max_total_score == 8
        assert result.percentage == 50.0

    def test_invalid_json_raises_value_error(self):
        """格式錯誤的 JSON 應拋出 ValueError，不能讓整個伺服器崩潰"""
        with pytest.raises(ValueError, match="AI 回傳格式錯誤"):
            self.provider._parse_response("這不是 JSON 格式")

    def test_empty_dimensions(self):
        """沒有構面時，分數應為 0"""
        raw = """{
            "dimension_scores": [],
            "overall_feedback": "無法評分。",
            "confidence": 0.3
        }"""
        result = self.provider._parse_response(raw)
        assert result.total_score == 0
        assert result.max_total_score == 0
        assert result.percentage == 0


# ──────────────────────────────────────────
# 測試 evaluate_stream（Mock Gemini API 呼叫）
# ──────────────────────────────────────────

class TestEvaluateStream:

    @pytest.mark.asyncio
    async def test_stream_yields_dimension_then_complete(self):
        """SSE 串流應先 yield 各構面，最後 yield score_complete"""
        provider = GeminiProvider()

        mock_response = MagicMock()
        mock_response.text = """{
            "dimension_scores": [
                {"name": "構面A", "score": 3, "max_score": 4, "reason": "不錯", "evidence": "..."},
                {"name": "構面B", "score": 4, "max_score": 4, "reason": "優秀", "evidence": "..."}
            ],
            "overall_feedback": "整體良好。",
            "confidence": 0.9
        }"""

        with patch.object(
            provider, "evaluate", new=AsyncMock(return_value=provider._parse_response(mock_response.text))
        ):
            chunks = []
            async for chunk in provider.evaluate_stream("學生作答", "rubric", "prompt"):
                chunks.append(chunk)

        types = [c["type"] for c in chunks]
        assert types.count("dimension_score") == 2
        assert types[-1] == "score_complete"

    @pytest.mark.asyncio
    async def test_stream_complete_contains_pass_info(self):
        """score_complete 應包含 total_score、percentage、overall_feedback"""
        provider = GeminiProvider()

        mock_response_text = """{
            "dimension_scores": [
                {"name": "A", "score": 4, "max_score": 4, "reason": "", "evidence": ""}
            ],
            "overall_feedback": "很好！",
            "confidence": 0.95
        }"""

        with patch.object(
            provider, "evaluate", new=AsyncMock(return_value=provider._parse_response(mock_response_text))
        ):
            chunks = []
            async for chunk in provider.evaluate_stream("作答", "rubric", "prompt"):
                chunks.append(chunk)

        complete = next(c for c in chunks if c["type"] == "score_complete")
        assert "total_score" in complete["data"]
        assert "percentage" in complete["data"]
        assert "overall_feedback" in complete["data"]
