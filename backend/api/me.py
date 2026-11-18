"""
登入者自己的設定

  GET    /api/me/api-key   自備 Gemini 金鑰的狀態：{has_key, key_hint, verified_at}（永不回傳金鑰）
  PUT    /api/me/api-key   {api_key}：驗證後加密儲存（覆蓋舊的）
  DELETE /api/me/api-key   刪除

用「自備金鑰」方案的邀請碼加入課程的學生，存好金鑰後作答會用自己的金鑰、不限次數（core/access.py）。
前端：/student/profile。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from core.auth import CurrentUser, get_current_user
from core.user_api_keys import (
    InvalidApiKey,
    KeyStorageNotConfigured,
    KeyVerificationUnavailable,
    delete_key,
    key_status,
    normalize_key,
    save_key,
    verify_key,
)
from database.client import get_supabase

router = APIRouter(prefix="/api/me", tags=["Me"])


class SaveApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _not_configured() -> HTTPException:
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "KEY_STORAGE_NOT_CONFIGURED",
        "平台尚未開放自備金鑰（後端未設定 API_KEY_ENCRYPTION_KEY），請聯絡老師",
    )


@router.get("/api-key")
async def get_api_key_status(user: CurrentUser = Depends(get_current_user)):
    return {"data": key_status(get_supabase(), user.id)}


@router.put("/api-key")
async def put_api_key(body: SaveApiKeyRequest, user: CurrentUser = Depends(get_current_user)):
    try:
        key = normalize_key(body.api_key)
        await verify_key(key)
        data = save_key(get_supabase(), user.id, key)
    except InvalidApiKey as e:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_API_KEY", str(e))
    except KeyVerificationUnavailable:
        raise _error(status.HTTP_502_BAD_GATEWAY, "KEY_VERIFY_FAILED", "暫時無法向 Google 確認金鑰，請稍後再試")
    except KeyStorageNotConfigured:
        raise _not_configured()
    return {"data": data, "message": "金鑰已驗證並儲存"}


@router.delete("/api-key")
async def remove_api_key(user: CurrentUser = Depends(get_current_user)):
    delete_key(get_supabase(), user.id)
    return {"data": {"has_key": False, "key_hint": None, "verified_at": None}, "message": "已刪除金鑰"}
