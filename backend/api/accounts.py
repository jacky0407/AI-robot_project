"""
帳號管理 API（#87，migration 027）：停用、恢復、去識別化、永久刪除（30 天回收期）、復原、清除到期帳號

  GET  /api/admin/accounts?status=&role=&q=     帳號清單（教授、助理），每個附 actions（目前登入者可以做的事）
  POST /api/admin/accounts/{id}/disable         停用（教授、助理；助理只能對學生）         body: {reason}
  POST /api/admin/accounts/{id}/enable          恢復（同上）                                 body: {reason?}
  POST /api/admin/accounts/{id}/deidentify      去識別化（只限教授、只對學生；不能復原）     body: {reason, confirm: "去識別化"}
  POST /api/admin/accounts/{id}/delete          永久刪除（只限教授；30 天後才真的刪除）     body: {reason, confirm_email}
  POST /api/admin/accounts/{id}/restore         30 天內復原（只限教授）
  POST /api/admin/accounts/purge-due            清除回收期已過的帳號（只限教授）

永久刪除與清除到期帳號：要 5 分鐘內重新輸入過兩步驟驗證碼（core/auth.require_recent_mfa），
並要輸入對方的 Email 確認。規則與權限在 core/accounts.py；每個操作都寫進操作紀錄。
"""

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from core import accounts
from core.accounts import STATUS_LABELS, AccountError, allowed_actions, status_of
from core.auth import CurrentUser, require_owner, require_recent_mfa, require_staff
from database.client import get_supabase

router = APIRouter(prefix="/api/admin/accounts", tags=["Accounts"])

UNDEFINED_COLUMN = "42703"
DEIDENTIFY_CONFIRM = "去識別化"


class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class ReasonRequest(_Body):
    reason: str = Field(min_length=2, max_length=500)


class OptionalReasonRequest(_Body):
    reason: Optional[str] = Field(default=None, max_length=500)


class DeidentifyRequest(ReasonRequest):
    # 要打出「去識別化」四個字，避免誤按
    confirm: str


class DeleteRequest(ReasonRequest):
    # 要輸入對方的 Email，避免刪錯人
    confirm_email: str = Field(min_length=3, max_length=320)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _run(fn, *args, **kwargs):
    """執行 core/accounts.py 的操作；AccountError 與還沒跑 migration 027 轉成 HTTP 錯誤"""
    try:
        return fn(*args, **kwargs)
    except AccountError as e:
        raise _error(e.status_code, e.code, e.message)
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) == UNDEFINED_COLUMN:
            raise _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "MIGRATION_REQUIRED",
                "資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/027_account_lifecycle.sql",
            )
        raise


def _output(actor: CurrentUser, profile: dict) -> dict:
    account_status = status_of(profile)
    return {
        "id": profile["id"],
        "email": profile.get("email"),
        "full_name": profile.get("full_name"),
        "role": profile.get("role"),
        "account_status": account_status,
        "status_label": STATUS_LABELS[account_status],
        "status_reason": profile.get("status_reason"),
        "status_changed_at": profile.get("status_changed_at"),
        "deletion_scheduled_for": profile.get("deletion_scheduled_for"),
        "created_at": profile.get("created_at"),
        "actions": allowed_actions(actor, profile),
    }


@router.get("")
async def list_accounts(
    account_status: Optional[Literal["active", "disabled", "deidentified", "pending_deletion"]] = Query(None, alias="status"),
    role: Optional[Literal["owner", "assistant", "student", "explorer"]] = None,
    q: Optional[str] = Query(None, max_length=100),
    user: CurrentUser = Depends(require_staff),
):
    """帳號清單（新的在前）；q 搜尋姓名或 Email"""
    db = get_supabase()
    query = db.table("profiles").select(accounts.PROFILE_FIELDS)
    if role:
        query = query.eq("role", role)
    rows = _run(lambda: query.order("created_at", desc=True).execute()).data or []

    keyword = (q or "").strip().lower()
    result = []
    for r in rows:
        if account_status and status_of(r) != account_status:
            continue
        if keyword and keyword not in f"{r.get('full_name') or ''} {r.get('email') or ''}".lower():
            continue
        result.append(_output(user, r))
    return {"data": result, "due_for_purge": len(_run(accounts.due_for_purge, db)) if user.role == "owner" else 0}


@router.post("/purge-due")
async def purge_due_accounts(user: CurrentUser = Depends(require_owner)):
    """清除回收期（30 天）已過的帳號：真正刪除，不能復原"""
    require_recent_mfa(user)
    db = get_supabase()
    result = _run(accounts.purge_due, db, user)
    message = f"已刪除 {len(result['purged'])} 個帳號"
    if result["failed"]:
        message += f"，{len(result['failed'])} 個刪除失敗（請稍後再試，詳情見後端 log）"
    return {"data": result, "message": message}


@router.post("/{user_id}/disable")
async def disable_account(user_id: UUID, body: ReasonRequest, user: CurrentUser = Depends(require_staff)):
    """停用：不能登入，資料全部保留，可以隨時恢復"""
    db = get_supabase()
    updated = _run(accounts.disable, db, user, str(user_id), body.reason)
    return {"data": _output(user, _run(accounts.get_profile, db, updated["id"])), "message": "已停用，這個帳號不能再登入"}


@router.post("/{user_id}/enable")
async def enable_account(
    user_id: UUID,
    body: Optional[OptionalReasonRequest] = None,
    user: CurrentUser = Depends(require_staff),
):
    """恢復停用的帳號"""
    db = get_supabase()
    updated = _run(accounts.enable, db, user, str(user_id), body.reason if body else None)
    return {"data": _output(user, _run(accounts.get_profile, db, updated["id"])), "message": "已恢復，這個帳號可以登入了"}


@router.post("/{user_id}/deidentify")
async def deidentify_account(user_id: UUID, body: DeidentifyRequest, user: CurrentUser = Depends(require_owner)):
    """去識別化：姓名、Email 換成匿名研究編號，學習歷程保留；不能復原"""
    if body.confirm != DEIDENTIFY_CONFIRM:
        raise _error(status.HTTP_400_BAD_REQUEST, "CONFIRM_MISMATCH", f"請輸入「{DEIDENTIFY_CONFIRM}」確認")
    db = get_supabase()
    updated = _run(accounts.deidentify, db, user, str(user_id), body.reason)
    return {
        "data": _output(user, _run(accounts.get_profile, db, updated["id"])),
        "message": f"已去識別化，這個帳號現在顯示為 {updated.get('full_name')}",
    }


@router.post("/{user_id}/delete")
async def delete_account(user_id: UUID, body: DeleteRequest, user: CurrentUser = Depends(require_owner)):
    """永久刪除：先進入 30 天回收期（不能登入，可以復原），到期後由「清除到期帳號」真正刪除"""
    require_recent_mfa(user)
    db = get_supabase()
    target = _run(accounts.get_profile, db, str(user_id))
    if body.confirm_email.strip().lower() != (target.get("email") or "").strip().lower():
        raise _error(status.HTTP_400_BAD_REQUEST, "CONFIRM_MISMATCH", "輸入的 Email 和這個帳號不同，請再確認一次")
    updated = _run(accounts.request_deletion, db, user, str(user_id), body.reason)
    return {
        "data": _output(user, _run(accounts.get_profile, db, updated["id"])),
        "message": f"已排定刪除：{accounts.RECOVERY_DAYS} 天內可以復原，之後由「清除到期帳號」真正刪除",
    }


@router.post("/{user_id}/restore")
async def restore_account(user_id: UUID, user: CurrentUser = Depends(require_owner)):
    """30 天回收期內復原，回到申請刪除前的狀態"""
    db = get_supabase()
    updated = _run(accounts.restore, db, user, str(user_id))
    return {"data": _output(user, _run(accounts.get_profile, db, updated["id"])), "message": "已復原，取消刪除"}
