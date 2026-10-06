"""
單元測試：錯誤分類偵測 (core/ai/error_detector.py)

重點：
  - 教授沒定義 error_taxonomy 時，一次 LLM 都不能呼叫（省額度）
  - AI 自創的 code 必須被丟掉
  - 引不出原文佐證的標記必須被丟掉——教授看到的每一項都要能回溯原文
"""

import json
import pytest
from unittest.mock import AsyncMock

from core.ai.error_detector import (
    ErrorDetector,
    parse_error_taxonomy,
    format_taxonomy_for_prompt,
    DEFAULT_SEVERITY,
)


class FakeMessage:
    def __init__(self, content):
        self.content = content


TAXONOMY = [
    {
        "code": "diagnosis_only",
        "label": "僅列診斷名稱",
        "description": "只寫障礙類別，沒有具體行為",
        "severity": "high",
        "related_dimension": "功能性描述",
    },
    {
        "code": "deficit_language",
        "label": "缺陷導向用語",
        "description": "以否定句描述學生",
        "severity": "medium",
    },
]


def detection(items) -> str:
    return json.dumps({"detected": items})


class TestParseErrorTaxonomy:

    def test_parses_valid(self):
        result = parse_error_taxonomy(TAXONOMY)
        assert len(result) == 2
        assert result[0]["code"] == "diagnosis_only"

    def test_none_returns_empty(self):
        assert parse_error_taxonomy(None) == []

    def test_non_list_returns_empty(self):
        assert parse_error_taxonomy({"code": "x"}) == []

    def test_skips_items_without_code_or_label(self):
        result = parse_error_taxonomy([
            {"code": "ok", "label": "可以"},
            {"code": "", "label": "沒有 code"},
            {"code": "no_label"},
        ])
        assert [r["code"] for r in result] == ["ok"]

    def test_invalid_severity_falls_back(self):
        result = parse_error_taxonomy([{"code": "a", "label": "A", "severity": "超級嚴重"}])
        assert result[0]["severity"] == DEFAULT_SEVERITY

    def test_format_for_prompt_includes_code_and_label(self):
        text = format_taxonomy_for_prompt(parse_error_taxonomy(TAXONOMY))
        assert "diagnosis_only" in text
        assert "僅列診斷名稱" in text
        assert "功能性描述" in text


class TestDetect:

    @pytest.mark.asyncio
    async def test_no_taxonomy_skips_llm_entirely(self):
        """教授沒定義分類時不該浪費一次 API 呼叫"""
        llm = AsyncMock()
        detector = ErrorDetector(llm=llm)

        assert await detector.detect("任何作答", None) == []
        assert await detector.detect("任何作答", []) == []
        assert llm.ainvoke.await_count == 0

    @pytest.mark.asyncio
    async def test_detects_and_enriches_from_taxonomy(self):
        """label 與 severity 應取自教授的定義，不採信 AI 自己填的"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(detection([
            {"code": "diagnosis_only", "evidence": "「診斷為自閉症」", "explanation": "只寫診斷"},
        ])))
        detector = ErrorDetector(llm=llm)

        result = await detector.detect("小明診斷為自閉症。", TAXONOMY)

        assert len(result) == 1
        assert result[0]["label"] == "僅列診斷名稱"
        assert result[0]["severity"] == "high"
        assert result[0]["evidence"] == "「診斷為自閉症」"

    @pytest.mark.asyncio
    async def test_rejects_invented_codes(self):
        """AI 自創清單外的 code 一律丟掉"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(detection([
            {"code": "我自己發明的錯誤", "evidence": "某段原文"},
            {"code": "deficit_language", "evidence": "「無法與同伴互動」"},
        ])))
        detector = ErrorDetector(llm=llm)

        result = await detector.detect("作答", TAXONOMY)
        assert [r["code"] for r in result] == ["deficit_language"]

    @pytest.mark.asyncio
    async def test_rejects_detections_without_evidence(self):
        """標記了卻引不出原文 → 不可信，丟掉"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(detection([
            {"code": "diagnosis_only", "evidence": ""},
            {"code": "deficit_language", "evidence": "   "},
        ])))
        detector = ErrorDetector(llm=llm)

        assert await detector.detect("作答", TAXONOMY) == []

    @pytest.mark.asyncio
    async def test_deduplicates_codes(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(detection([
            {"code": "diagnosis_only", "evidence": "第一次"},
            {"code": "diagnosis_only", "evidence": "第二次"},
        ])))
        detector = ErrorDetector(llm=llm)

        result = await detector.detect("作答", TAXONOMY)
        assert len(result) == 1
        assert result[0]["evidence"] == "第一次"

    @pytest.mark.asyncio
    async def test_empty_detection_returns_empty(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(detection([])))
        detector = ErrorDetector(llm=llm)
        assert await detector.detect("很好的作答", TAXONOMY) == []

    @pytest.mark.asyncio
    async def test_handles_fenced_json(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(
            '```json\n{"detected": [{"code": "diagnosis_only", "evidence": "「自閉症」"}]}\n```'
        ))
        detector = ErrorDetector(llm=llm)
        result = await detector.detect("作答", TAXONOMY)
        assert result[0]["code"] == "diagnosis_only"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty_not_raise(self):
        """錯誤分類不是關鍵路徑，失敗不該讓整個評分掛掉"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("掛了"))
        detector = ErrorDetector(llm=llm)
        assert await detector.detect("作答", TAXONOMY) == []

    @pytest.mark.asyncio
    async def test_malformed_response_returns_empty(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage("完全不是 JSON"))
        detector = ErrorDetector(llm=llm)
        assert await detector.detect("作答", TAXONOMY) == []

    @pytest.mark.asyncio
    async def test_detected_not_a_list_returns_empty(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(json.dumps({"detected": "壞掉"})))
        detector = ErrorDetector(llm=llm)
        assert await detector.detect("作答", TAXONOMY) == []
