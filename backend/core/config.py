from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Supabase（由成員二建立後填入）
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Gemini API
    gemini_api_key: str = ""

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
