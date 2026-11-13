"""
學生自備 Gemini API 金鑰的儲存（migration 017；「邀請碼與自備金鑰設計提案」第二階段）

安全原則：
  - 金鑰用 API_KEY_ENCRYPTION_KEY（Fernet）加密後才寫進資料庫；資料庫、log、API 回應都沒有明文
  - API 只回 has_key / key_hint（前 4 碼…末 4 碼）/ verified_at
  - 解密只在評分當下，由 api/practice.py 透過 core/ai/llm_keys.use_api_key() 用在那一次請求
  - 儲存前先向 Google 驗證金鑰可用（列出模型，不產生內容、不花費額度）
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from core.config import get_settings

logger = logging.getLogger(__name__)

TABLE = "user_api_keys"
# Google API 金鑰：英數、底線、連字號；長度留寬一點，不寫死 39
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{30,100}$")
VERIFY_TIMEOUT_SECONDS = 15


class KeyStorageNotConfigured(Exception):
    """後端沒有設定 API_KEY_ENCRYPTION_KEY"""


class InvalidApiKey(Exception):
    """格式不對，或 Google 回覆這把金鑰不能用"""


class KeyVerificationUnavailable(Exception):
    """連不到 Google，無法確認金鑰（不是金鑰的錯）"""


def _fernet():
    from cryptography.fernet import Fernet

    secret = get_settings().api_key_encryption_key
    if not secret:
        raise KeyStorageNotConfigured()
    try:
        return Fernet(secret.encode())
    except (ValueError, TypeError) as e:
        raise KeyStorageNotConfigured() from e


def key_hint(api_key: str) -> str:
    return f"{api_key[:4]}…{api_key[-4:]}"


def normalize_key(raw: str) -> str:
    key = (raw or "").strip()
    if not KEY_PATTERN.match(key):
        raise InvalidApiKey("金鑰格式不正確，請到 Google AI Studio 複製完整的 API 金鑰")
    return key


def _list_models(api_key: str) -> None:
    from google import genai

    client = genai.Client(api_key=api_key)
    # 只要能列出一個模型就代表金鑰有效；不產生內容，不花額度
    next(iter(client.models.list()), None)


async def verify_key(api_key: str) -> None:
    """向 Google 確認金鑰可用。金鑰無效 → InvalidApiKey；連不到 → KeyVerificationUnavailable"""
    try:
        await asyncio.wait_for(asyncio.to_thread(_list_models, api_key), timeout=VERIFY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
        raise KeyVerificationUnavailable() from e
    except Exception as e:  # noqa: BLE001 - google-genai 的例外型別依版本不同
        text = str(e).lower()
        if any(w in text for w in ("api key", "api_key", "permission", "401", "403", "invalid", "unauthenticated")):
            raise InvalidApiKey("Google 回覆這把金鑰無法使用，請確認金鑰正確、且已啟用 Gemini API") from e
        logger.warning(f"驗證金鑰時連線失敗：{type(e).__name__}")  # 不記錄例外內容，避免夾帶金鑰
        raise KeyVerificationUnavailable() from e


def save_key(db, user_id: str, api_key: str) -> dict:
    """加密後寫入（每人一把，覆蓋舊的）。回傳狀態（不含金鑰）"""
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "user_id": user_id,
        "encrypted_key": _fernet().encrypt(api_key.encode()).decode(),
        "key_hint": key_hint(api_key),
        "verified_at": now,
        "updated_at": now,
    }
    existing = db.table(TABLE).select("user_id").eq("user_id", user_id).execute().data or []
    if existing:
        db.table(TABLE).update(row).eq("user_id", user_id).execute()
    else:
        db.table(TABLE).insert(row).execute()
    return {"has_key": True, "key_hint": row["key_hint"], "verified_at": now}


def key_status(db, user_id: str) -> dict:
    rows = db.table(TABLE).select("key_hint, verified_at").eq("user_id", user_id).execute().data or []
    if not rows:
        return {"has_key": False, "key_hint": None, "verified_at": None}
    return {"has_key": True, "key_hint": rows[0].get("key_hint"), "verified_at": rows[0].get("verified_at")}


def has_key(db, user_id: str) -> bool:
    """作答權限判斷用；還沒跑 migration 017 時視為沒有金鑰"""
    try:
        return key_status(db, user_id)["has_key"]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取自備金鑰狀態失敗（請確認已執行 migration 017）：{type(e).__name__}")
        return False


def load_decrypted_key(db, user_id: str) -> Optional[str]:
    """評分當下取出明文金鑰；沒有或解不開回 None（例如 API_KEY_ENCRYPTION_KEY 被換掉）"""
    try:
        rows = db.table(TABLE).select("encrypted_key").eq("user_id", user_id).execute().data or []
        if not rows:
            return None
        return _fernet().decrypt(rows[0]["encrypted_key"].encode()).decode()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"取出自備金鑰失敗：{type(e).__name__}")
        return None


def delete_key(db, user_id: str) -> None:
    db.table(TABLE).delete().eq("user_id", user_id).execute()
