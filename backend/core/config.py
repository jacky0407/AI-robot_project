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
    # 不打分數的對話功能用的較便宜模型（聊天室調度員、聊天機器人回覆、學習助教、不評分機器人的回饋）。
    # 不填就跟 GEMINI_MODEL 一樣；評分、錯誤偵測、提示、跨步驟檢核一律用 GEMINI_MODEL。
    gemini_light_model: str = ""

    # 學習助教不直接給答案；同一個問題問到第幾次才可以直接給答案（老師 11/24 確認的規則，次數待老師定）
    tutor_answer_after: int = 3
    # 學習助教（RAG，#67）切段與搜尋用的向量模型；維度固定 768（migration 021 的 vector(768)）
    gemini_embedding_model: str = "gemini-embedding-001"

    # 環境
    environment: str = "development"
    # 允許呼叫後端的前端網址（CORS）。多個網址用逗號分隔，例如：
    #   FRONTEND_URL=https://xxx.vercel.app,http://localhost:3000
    frontend_url: str = "http://localhost:3000"

    # 單次 LLM 呼叫的逾時秒數。Gemini 預設思考模式評一個構面常要 30～50 秒，
    # 太短會「已經在算了卻被切斷重來」，重試又多吃額度（KNOWN_GAPS #17）
    llm_timeout_seconds: float = 90.0

    # 學生自備 Gemini 金鑰的加密金鑰（Fernet，base64 32 bytes）。沒設定時自備金鑰功能回 503。
    # 產生：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # ⚠️ 換掉這把會讓已存的學生金鑰全部無法解開（學生要重新填）
    api_key_encryption_key: str = ""

    @property
    def light_model(self) -> str:
        """對話類功能實際用的模型：有設 GEMINI_LIGHT_MODEL 就用它，否則用 GEMINI_MODEL"""
        return (self.gemini_light_model or "").strip() or self.gemini_model

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
