"""
單元測試：Gemini 模型設定集中在 GEMINI_MODEL

確保：
  1. 預設模型是 config.DEFAULT_GEMINI_MODEL
  2. .env / 環境變數的 GEMINI_MODEL 可以覆蓋
  3. 每個 Agent 都讀設定，沒有任何檔案把模型名稱寫死
"""

import re
from pathlib import Path

import pytest

from core.config import DEFAULT_GEMINI_MODEL, Settings, get_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
# 像 "gemini-2.5-flash"、'gemini-3.5-flash' 這種寫死的模型字串
HARDCODED_MODEL = re.compile(r"""["']gemini-\d""")


@pytest.fixture
def model_env(monkeypatch):
    """設定 GEMINI_MODEL 並清掉 get_settings 的快取，測完還原"""
    def _set(value: str):
        monkeypatch.setenv("GEMINI_MODEL", value)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        get_settings.cache_clear()
    yield _set
    get_settings.cache_clear()


def test_default_model(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert Settings(_env_file=None).gemini_model == DEFAULT_GEMINI_MODEL == "gemini-3.5-flash"


def test_env_overrides_model(model_env):
    model_env("gemini-3.8-flash")
    assert get_settings().gemini_model == "gemini-3.8-flash"


def test_no_hardcoded_model_names_outside_config():
    offenders = []
    for folder in ("core", "api"):
        for path in (BACKEND_DIR / folder).rglob("*.py"):
            if path.name == "config.py":
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if HARDCODED_MODEL.search(line):
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}:{lineno}: {line.strip()}")
    assert not offenders, "模型名稱請改用 settings.gemini_model：\n" + "\n".join(offenders)


def _model_name(llm) -> str:
    # langchain-google-genai 可能把模型存成 "models/xxx"
    return str(getattr(llm, "model", "")).removeprefix("models/")


def test_every_agent_uses_configured_model(model_env):
    model_env("gemini-test-model")

    from core.ai.agents.evaluator_agent import EvaluatorAgent
    from core.ai.agents.coherence_agent import CoherenceAgent
    from core.ai.error_detector import ErrorDetector
    from core.ai.hint_engine import HintEngine
    from core.ai.gemini_provider import GeminiProvider

    assert _model_name(EvaluatorAgent().llm) == "gemini-test-model"
    assert _model_name(CoherenceAgent().llm) == "gemini-test-model"
    assert _model_name(ErrorDetector().llm) == "gemini-test-model"
    assert _model_name(HintEngine().llm) == "gemini-test-model"
    assert GeminiProvider().model_id == "gemini-test-model"


# ──────────────────────────────────────────
# CORS 與 LLM 逾時（部署用設定）
# ──────────────────────────────────────────

def test_single_frontend_url_is_one_origin():
    assert Settings(_env_file=None, frontend_url="http://localhost:3000").cors_origins == [
        "http://localhost:3000"
    ]


def test_frontend_url_accepts_comma_separated_origins():
    """雲端（Vercel）與本機開發要能同時連線"""
    s = Settings(_env_file=None, frontend_url=" https://demo.vercel.app/ , http://localhost:3000,, ")
    assert s.cors_origins == ["https://demo.vercel.app", "http://localhost:3000"]


def test_llm_timeout_default_is_90_seconds(monkeypatch):
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    assert Settings(_env_file=None).llm_timeout_seconds == 90.0


def test_llm_timeout_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "45")
    assert Settings(_env_file=None).llm_timeout_seconds == 45.0


# ──────────────────────────────────────────
# 較便宜的對話模型（GEMINI_LIGHT_MODEL）
# ──────────────────────────────────────────

@pytest.fixture
def light_env(monkeypatch):
    """設定 GEMINI_MODEL / GEMINI_LIGHT_MODEL，清掉設定與 client 的快取，測完還原"""
    from core.ai import llm_keys

    def _set(model: str, light: str | None):
        monkeypatch.setenv("GEMINI_MODEL", model)
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        if light is None:
            monkeypatch.delenv("GEMINI_LIGHT_MODEL", raising=False)
        else:
            monkeypatch.setenv("GEMINI_LIGHT_MODEL", light)
        get_settings.cache_clear()
        llm_keys._platform_llms.clear()
    yield _set
    get_settings.cache_clear()
    llm_keys._platform_llms.clear()


def test_light_model_falls_back_to_main_model(light_env):
    light_env("gemini-main", None)
    assert get_settings().light_model == "gemini-main"


def test_light_model_can_be_set(light_env):
    light_env("gemini-main", "gemini-lite")
    assert get_settings().light_model == "gemini-lite"
    assert get_settings().gemini_model == "gemini-main"


def test_chat_features_use_light_model_and_scoring_does_not(light_env):
    """調度員、聊天回覆、不評分的回饋用便宜模型；評分、提示、錯誤偵測、跨步驟檢核維持主模型"""
    light_env("gemini-main", "gemini-lite")
    from core.ai.agents.coherence_agent import CoherenceAgent
    from core.ai.agents.evaluator_agent import EvaluatorAgent
    from core.ai.error_detector import ErrorDetector
    from core.ai.hint_engine import HintEngine
    from core.ai.llm_keys import chat_llm

    assert _model_name(chat_llm(0.0, light=True)) == "gemini-lite"
    assert _model_name(chat_llm(0.5)) == "gemini-main"
    for agent in (EvaluatorAgent(), CoherenceAgent(), ErrorDetector(), HintEngine()):
        assert _model_name(agent.llm) == "gemini-main"


def test_student_key_also_uses_light_model(light_env):
    light_env("gemini-main", "gemini-lite")
    from core.ai.llm_keys import chat_llm, use_api_key

    with use_api_key("student-key"):
        assert _model_name(chat_llm(0.5, light=True)) == "gemini-lite"


@pytest.mark.asyncio
async def test_agents_request_light_model(monkeypatch):
    """三個對話 Agent 都要帶 light=True 呼叫 chat_llm"""
    from unittest.mock import AsyncMock

    from core.ai.agents import chat_agents, feedback_agent

    calls = []
    fake = lambda temperature, *, light=False: calls.append(light) or object()  # noqa: E731
    monkeypatch.setattr(chat_agents, "chat_llm", fake)
    monkeypatch.setattr(feedback_agent, "chat_llm", fake)
    monkeypatch.setattr(chat_agents, "invoke_json", AsyncMock(return_value={"bot": 1, "reason": ""}))
    monkeypatch.setattr(chat_agents, "invoke_with_retry", AsyncMock(return_value="好"))
    monkeypatch.setattr(feedback_agent, "invoke_with_retry", AsyncMock(return_value="好"))

    bots = [chat_agents.ChatBot(tool_id="1", name="甲"), chat_agents.ChatBot(tool_id="2", name="乙")]
    await chat_agents.RouterAgent().route("你好", [], bots)
    await chat_agents.ChatBotAgent().reply(bots[0], bots, [], "你好")
    await feedback_agent.FeedbackAgent().feedback("作答", "你是老師")
    assert calls == [True, True, True]
