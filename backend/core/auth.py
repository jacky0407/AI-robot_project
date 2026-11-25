"""
登入驗證：從 Authorization: Bearer <supabase access token> 取得目前登入者

用法（FastAPI dependency）：
    @router.post("/xxx")
    async def handler(user: CurrentUser = Depends(get_current_user)): ...

    @router.post("/teacher-only")
    async def handler(user: CurrentUser = Depends(require_staff)): ...

身分一律以 token 為準，不相信前端在 body 裡傳來的 user_id（見 docs/KNOWN_GAPS.md #8）。
角色讀 profiles.role；只能由後台或後端（service_role）修改，見 migrations/006。

兩步驟驗證（#90）：教師端（owner / assistant）一律要完成兩步驟驗證才能用教師端 API。
  - Supabase 的 access token 裡有 aal：aal1 = 只輸入密碼；aal2 = 已輸入驗證器 App 的 6 位數
  - require_staff 檢查 aal2，沒有就回 403 MFA_REQUIRED（前端導到 /mfa 設定或輸入驗證碼）
  - 緊急狀況（例如 Supabase 的兩步驟驗證故障）可以在 Render 設 STAFF_MFA_REQUIRED=false 暫時關掉
"""

import base64
import json

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase_auth.errors import AuthError

from core.config import get_settings
from database.client import get_supabase

# 對應 profiles.role 的 CHECK：owner / assistant 是教師端，student / explorer 是學生端
STAFF_ROLES = ("owner", "assistant")

# 用 HTTPBearer 讀 header，/docs 才會出現 Authorize 按鈕；
# auto_error=False：沒帶或格式錯時回 None，由下面統一回 401 與錯誤格式
bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token")


@dataclass(frozen=True)
class CurrentUser:
    id: str
    role: str
    # 這次登入的驗證等級（token 的 aal）：aal1 = 只有密碼；aal2 = 完成兩步驟驗證。
    # get_current_user 一律從 token 讀出來填（讀不到當 aal1）；
    # None 只會出現在單元測試直接建立的 CurrentUser（測試各 API 時不檢查兩步驟驗證）
    aal: Optional[str] = None

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def token_aal(token: str) -> str:
    """
    從 access token 讀出 aal（aal1 / aal2）。
    只在 supabase.auth.get_user(token) 驗證過 token 之後呼叫，所以這裡只解碼、不再驗簽；
    格式不對一律當 aal1（最嚴格），不會因此放行。
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return "aal1"
    return "aal2" if isinstance(claims, dict) and claims.get("aal") == "aal2" else "aal1"


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    token = credentials.credentials.strip() if credentials else ""
    # 一定要先擋掉空 token：get_user(None) 會改讀 client 自己的 session
    if not token:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "請先登入")

    supabase = get_supabase()
    try:
        res = supabase.auth.get_user(token)
    except AuthError:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "登入已失效，請重新登入")
    if not res or not res.user:
        raise _error(status.HTTP_401_UNAUTHORIZED, "UNAUTHORIZED", "登入已失效，請重新登入")

    user_id = res.user.id
    profile = (
        supabase.table("profiles")
        .select("role, is_active")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not profile.data:
        raise _error(status.HTTP_403_FORBIDDEN, "PROFILE_NOT_FOUND", "找不到使用者資料，請重新註冊")

    row = profile.data[0]
    if row.get("is_active") is False:
        raise _error(status.HTTP_403_FORBIDDEN, "ACCOUNT_DISABLED", "此帳號已停用")

    return CurrentUser(id=user_id, role=row.get("role") or "student", aal=token_aal(token))


def require_staff(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_staff:
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有教師或助教可以執行此操作")
    # aal 是 None 代表不是從 token 來的（只有單元測試），不檢查
    if user.aal is not None and user.aal != "aal2" and get_settings().staff_mfa_required:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "MFA_REQUIRED",
            "教師端需要完成兩步驟驗證：請重新整理頁面，依畫面指示設定或輸入驗證器 App 的 6 位數驗證碼",
        )
    return user


def require_owner(user: CurrentUser = Depends(require_staff)) -> CurrentUser:
    """只限教授（owner）；一樣要先通過 require_staff（含兩步驟驗證）"""
    if user.role != "owner":
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有教授可以執行此操作")
    return user
