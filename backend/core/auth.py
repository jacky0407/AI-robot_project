"""
登入驗證：從 Authorization: Bearer <supabase access token> 取得目前登入者

用法（FastAPI dependency）：
    @router.post("/xxx")
    async def handler(user: CurrentUser = Depends(get_current_user)): ...

    @router.post("/teacher-only")
    async def handler(user: CurrentUser = Depends(require_staff)): ...

身分一律以 token 為準，不相信前端在 body 裡傳來的 user_id（見 docs/KNOWN_GAPS.md #8）。
角色讀 profiles.role；只能由後台或後端（service_role）修改，見 migrations/006。
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase_auth.errors import AuthError

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

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


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

    return CurrentUser(id=user_id, role=row.get("role") or "student")


def require_staff(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_staff:
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有教師或助教可以執行此操作")
    return user
