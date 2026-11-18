"""
這次請求要用哪一把 Gemini 金鑰

預設用平台的 GEMINI_API_KEY（.env / Render）。學生用「自備金鑰」方案的邀請碼加入課程時，
api/practice.py 會在那次評分的範圍內用 use_api_key() 換成學生自己的金鑰：
各 Agent 的 llm 屬性先看這裡有沒有指定金鑰，有就用它建一個 client（不快取，用完即丟）。

用 ContextVar 而不是改 Agent 的屬性：Agent 是整個程式共用的單例，
同時有其他學生在評分時，改屬性會讓別人也用到這把金鑰。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_override: ContextVar[Optional[str]] = ContextVar("gemini_api_key_override", default=None)


@contextmanager
def use_api_key(api_key: Optional[str]) -> Iterator[None]:
    """在這個區塊內（同一個請求的 async 流程）改用指定的金鑰；None = 用平台金鑰"""
    token = _override.set(api_key)
    try:
        yield
    finally:
        _override.reset(token)


def override_api_key() -> Optional[str]:
    return _override.get()


def build_llm(api_key: str, temperature: float):
    """用指定金鑰建立 Gemini client（模型一律讀 GEMINI_MODEL）"""
    from langchain_google_genai import ChatGoogleGenerativeAI

    from core.config import get_settings

    return ChatGoogleGenerativeAI(
        model=get_settings().gemini_model,
        google_api_key=api_key,
        temperature=temperature,
    )
