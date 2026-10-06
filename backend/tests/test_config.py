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
