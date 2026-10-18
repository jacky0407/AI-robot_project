from pydantic_settings import BaseSettings
from functools import lru_cache

# 所有 Agent 共用的 Gemini 模型。要換模型時改 .env 的 GEMINI_MODEL 即可，
# 不要在個別檔案裡寫死模型名稱（tests/test_config.py 會檢查）。
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


class Settings(BaseSettings):
    # Supabase（由成員二建立後填入）
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Gemini API
    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL

    # 環境
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
