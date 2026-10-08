"""
單元測試：check_setup.py 的 Gemini 檢查（不打真的 API）
"""

from unittest.mock import AsyncMock

from check_setup import check_gemini, explain_gemini_error
from core.config import Settings


class FakeMessage:
    def __init__(self, content):
        self.content = content


def _settings(model="gemini-3.5-flash"):
    return Settings(_env_file=None, gemini_api_key="test", gemini_model=model)


def test_check_gemini_ok():
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=FakeMessage("OK"))
    ok, message = check_gemini(_settings(), llm=llm)
    assert ok
    assert "gemini-3.5-flash" in message


def test_check_gemini_failure_is_explained():
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("404 models/gemini-x is not found"))
    ok, message = check_gemini(_settings("gemini-x"), llm=llm)
    assert not ok
    assert "找不到模型 gemini-x" in message


def test_explain_permission_error():
    msg = explain_gemini_error(RuntimeError("403 PERMISSION_DENIED"), "gemini-2.5-flash")
    assert "2.5" in msg and "3.x" in msg


def test_explain_quota_error():
    msg = explain_gemini_error(RuntimeError("429 RESOURCE_EXHAUSTED"), "gemini-3.5-flash")
    assert "額度" in msg


def test_explain_permission_error_for_3x_model_does_not_blame_2_5():
    msg = explain_gemini_error(RuntimeError("403 PERMISSION_DENIED"), "gemini-3.5-flash")
    assert "2.5" not in msg
    assert "沒有權限" in msg


def test_explain_proxy_error_is_network():
    msg = explain_gemini_error(RuntimeError("ProxyError: 403 Forbidden"), "gemini-3.5-flash")
    assert "連不到" in msg
