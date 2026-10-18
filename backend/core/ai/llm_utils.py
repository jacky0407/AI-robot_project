"""
LLM 呼叫的共用工具

所有 Agent 呼叫 LLM 都應該經過這裡，統一處理：
  - 逾時（避免單一請求卡住整個 SSE 串流）
  - 重試（暫時性錯誤重試，格式錯誤不重試）
  - JSON 解析（清掉 Gemini 有時會加上的 markdown 圍籬）

為什麼要有這層：
  Gemini 即使被要求「只回 JSON」，實務上仍會偶爾包上 ```json ... ```，
  舊版直接 json.loads() 會失敗並降級成「評分失敗，請教師複核」，
  學生看到的是一個沒有理由的低分。這是可以救回來的，不該讓它降級。
"""

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

# 預設值：評分是學生等在畫面前的同步操作，不宜等太久
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0

# ```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMTimeoutError(Exception):
    """LLM 呼叫超過逾時上限"""


class LLMResponseFormatError(Exception):
    """LLM 回傳的內容無法解析成預期的 JSON 結構"""


def content_to_text(content) -> str:
    """
    把 LLM 回應的 content 統一轉成字串。

    較新的 Gemini 模型（3.x）在 LangChain 裡可能回傳「內容區塊清單」，
    例如 [{"type": "text", "text": "..."}, {"type": "thinking", ...}]，
    而不是單純的字串。這裡只取出文字區塊並接起來，思考過程不算進回應。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type", "text") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content)


def strip_code_fence(text: str) -> str:
    """
    去掉 LLM 回應外層的 markdown 圍籬。
    沒有圍籬時原樣回傳。
    """
    if not text:
        return ""
    match = _FENCE_RE.match(text)
    return match.group(1) if match else text.strip()


def parse_json_response(text: str, *, expect: type = dict):
    """
    把 LLM 回應解析成 JSON。

    比 json.loads() 多做三件事：
      1. 去掉 markdown 圍籬
      2. 圍籬去掉後仍失敗時，嘗試擷取第一個完整的 {...} 或 [...]
      3. 型別不符時拋出明確的例外，而不是讓呼叫端拿到怪東西

    Args:
        text:   LLM 的原始回應
        expect: 預期的頂層型別（dict 或 list）
    Raises:
        LLMResponseFormatError
    """
    cleaned = strip_code_fence(text)

    for candidate in (cleaned, _extract_first_json(cleaned)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, expect):
            return data
        raise LLMResponseFormatError(
            f"LLM 回傳的 JSON 頂層型別是 {type(data).__name__}，預期 {expect.__name__}"
        )

    preview = (cleaned or "")[:200]
    raise LLMResponseFormatError(f"無法從 LLM 回應解析出 JSON：{preview!r}")


def _extract_first_json(text: str) -> str | None:
    """
    從夾雜說明文字的回應中，擷取第一個成對的 {...} 或 [...]。
    用括號配對而非正規表達式，才能處理巢狀結構。
    """
    if not text:
        return None

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


async def invoke_with_retry(
    llm,
    messages: list,
    *,
    label: str = "llm",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF_SECONDS,
) -> str:
    """
    呼叫 LLM 並回傳文字內容，附逾時與重試。

    重試策略：
      - 逾時、連線錯誤、額度暫時用盡 → 重試（指數退避）
      - 其他例外一律視為暫時性問題重試，但保留最後一次的錯誤往外拋

    Args:
        llm:          具備 async ainvoke(messages) 的物件（LangChain ChatModel）
        messages:     傳給 ainvoke 的訊息列表
        label:        記錄用的標籤，方便從 log 追是哪一步失敗
        timeout:      單次呼叫的逾時秒數
        max_attempts: 總嘗試次數（含第一次）
    Returns:
        LLM 回應的文字內容
    Raises:
        LLMTimeoutError: 每一次嘗試都逾時
        Exception:       最後一次嘗試的原始例外
    """
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout)
            return content_to_text(getattr(response, "content", response))

        except asyncio.TimeoutError as e:
            last_error = LLMTimeoutError(f"{label} 第 {attempt} 次呼叫超過 {timeout} 秒")
            logger.warning(str(last_error))

        except Exception as e:  # noqa: BLE001 - 廠商 SDK 的例外型別不一致
            last_error = e
            logger.warning(f"{label} 第 {attempt} 次呼叫失敗：{e}")

        if attempt < max_attempts:
            await asyncio.sleep(backoff * (2 ** (attempt - 1)))

    logger.error(f"{label} 重試 {max_attempts} 次後仍失敗")
    raise last_error


async def invoke_json(
    llm,
    messages: list,
    *,
    label: str = "llm",
    expect: type = dict,
    **kwargs,
):
    """
    invoke_with_retry + parse_json_response 的組合，Agent 最常用的入口。

    Raises:
        LLMResponseFormatError: 重試後仍拿不到合法 JSON
    """
    raw = await invoke_with_retry(llm, messages, label=label, **kwargs)
    return parse_json_response(raw, expect=expect)
