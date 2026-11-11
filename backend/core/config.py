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
    # 允許呼叫後端的前端網址（CORS）。多個網址用逗號分隔，例如：
    #   FRONTEND_URL=https://xxx.vercel.app,http://localhost:3000
    frontend_url: str = "http://localhost:3000"

    # 單次 LLM 呼叫的逾時秒數。Gemini 預設思考模式評一個構面常要 30～50 秒，
    # 太短會「已經在算了卻被切斷重來」，重試又多吃額度（KNOWN_GAPS #17）
    llm_timeout_seconds: float = 90.0

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origins(self) -> list[str]:
        """FRONTEND_URL 拆成清單；去掉空白與結尾的 /（瀏覽器送的 Origin 不含結尾 /）"""
        return [u.strip().rstrip("/") for u in self.frontend_url.split(",") if u.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
