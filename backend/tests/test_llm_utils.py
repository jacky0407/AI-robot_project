"""
單元測試：LLM 呼叫共用工具 (core/ai/llm_utils.py)

重點：Gemini 實務上會加 markdown 圍籬、會逾時、會間歇性失敗，
這些都不該讓學生看到「評分失敗」。
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock

from core.ai.llm_utils import (
    content_to_text,
    strip_code_fence,
    parse_json_response,
    invoke_with_retry,
    invoke_json,
    LLMTimeoutError,
    LLMResponseFormatError,
)


class FakeMessage:
    def __init__(self, content):
        self.content = content


class TestContentToText:
    """Gemini 3.x 在 LangChain 可能回傳內容區塊清單，而不是字串"""

    def test_plain_string_unchanged(self):
        assert content_to_text("hello") == "hello"

    def test_none_becomes_empty(self):
        assert content_to_text(None) == ""

    def test_joins_text_blocks_and_skips_thinking(self):
        content = [
            {"type": "thinking", "thinking": "先想一下..."},
            {"type": "text", "text": '{"score": '},
            {"type": "text", "text": "3}"},
        ]
        assert content_to_text(content) == '{"score": 3}'

    def test_plain_strings_in_list(self):
        assert content_to_text(["a", "b"]) == "ab"

    @pytest.mark.asyncio
    async def test_invoke_json_handles_block_list(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage(
            [{"type": "text", "text": '```json\n{"ok": true}\n```'}]
        ))
        assert await invoke_json(llm, [], max_attempts=1) == {"ok": True}


class TestStripCodeFence:

    def test_plain_json_unchanged(self):
        assert strip_code_fence('{"a": 1}') == '{"a": 1}'

    def test_removes_json_fence(self):
        assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_removes_bare_fence(self):
        assert strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_handles_leading_whitespace(self):
        assert strip_code_fence('\n  ```json\n{"a": 1}\n```  \n') == '{"a": 1}'

    def test_empty_string(self):
        assert strip_code_fence("") == ""


class TestParseJsonResponse:

    def test_parses_plain_json(self):
        assert parse_json_response('{"score": 3}') == {"score": 3}

    def test_parses_fenced_json(self):
        """這是最常見的實際狀況：模型被要求只回 JSON，還是加了圍籬"""
        assert parse_json_response('```json\n{"score": 3}\n```') == {"score": 3}

    def test_extracts_json_from_surrounding_prose(self):
        """模型加了說明文字時，仍要能把 JSON 撈出來"""
        raw = '好的，以下是評分結果：\n{"score": 3, "reason": "不錯"}\n希望有幫助！'
        assert parse_json_response(raw)["score"] == 3

    def test_handles_nested_braces(self):
        raw = '前言 {"a": {"b": {"c": 1}}, "d": 2} 後記'
        assert parse_json_response(raw) == {"a": {"b": {"c": 1}}, "d": 2}

    def test_braces_inside_strings_do_not_break_extraction(self):
        raw = '說明 {"evidence": "他說「{這樣}」", "score": 2} 結束'
        result = parse_json_response(raw)
        assert result["score"] == 2

    def test_expects_list_when_asked(self):
        assert parse_json_response('[{"a": 1}]', expect=list) == [{"a": 1}]

    def test_wrong_top_level_type_raises(self):
        with pytest.raises(LLMResponseFormatError):
            parse_json_response('[1, 2, 3]', expect=dict)

    def test_unparseable_raises(self):
        with pytest.raises(LLMResponseFormatError):
            parse_json_response("這完全不是 JSON")

    def test_empty_raises(self):
        with pytest.raises(LLMResponseFormatError):
            parse_json_response("")


class TestInvokeWithRetry:

    @pytest.mark.asyncio
    async def test_returns_content_on_success(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage("ok"))
        assert await invoke_with_retry(llm, [], max_attempts=1) == "ok"

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        """間歇性失敗應該被重試救回來"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=[
            RuntimeError("暫時性錯誤"),
            FakeMessage("第二次成功"),
        ])
        result = await invoke_with_retry(llm, [], max_attempts=3, backoff=0)
        assert result == "第二次成功"
        assert llm.ainvoke.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_last_error_after_exhausting_attempts(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("一直失敗"))
        with pytest.raises(RuntimeError, match="一直失敗"):
            await invoke_with_retry(llm, [], max_attempts=2, backoff=0)
        assert llm.ainvoke.await_count == 2

    @pytest.mark.asyncio
    async def test_timeout_raises_llm_timeout_error(self):
        async def never_returns(*args, **kwargs):
            await asyncio.sleep(10)

        llm = AsyncMock()
        llm.ainvoke = never_returns
        with pytest.raises(LLMTimeoutError):
            await invoke_with_retry(llm, [], timeout=0.01, max_attempts=2, backoff=0)

    @pytest.mark.asyncio
    async def test_timeout_then_success(self):
        """第一次逾時、第二次回來，應該拿到結果而不是報錯"""
        calls = {"n": 0}

        async def slow_then_fast(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                await asyncio.sleep(10)
            return FakeMessage("終於回來了")

        llm = AsyncMock()
        llm.ainvoke = slow_then_fast
        result = await invoke_with_retry(llm, [], timeout=0.01, max_attempts=2, backoff=0)
        assert result == "終於回來了"


class TestInvokeJson:

    @pytest.mark.asyncio
    async def test_parses_fenced_response(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage('```json\n{"score": 4}\n```'))
        assert await invoke_json(llm, [], max_attempts=1) == {"score": 4}

    @pytest.mark.asyncio
    async def test_format_error_propagates(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage("不是 JSON"))
        with pytest.raises(LLMResponseFormatError):
            await invoke_json(llm, [], max_attempts=1)
