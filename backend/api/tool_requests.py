"""
機器人申請 API：自主模式的學生申請使用某支機器人，老師核准時設定到期日與每日次數

資料存在 tool_permissions（一筆 = 一次申請）：
    pending  → 等老師審核      approved → 已核准（可用到 expire_date）
    rejected → 老師拒絕或撤銷  expired  → 已過期（見下方說明）

「已過期」不靠排程改資料：status 為 approved 但 expire_date 已過（台灣時間），
API 一律回傳 expired。被拒絕或過期後可以再申請，會新增一筆，保留歷史。

額度（used_today 扣次數、每天歸零）由 practice API 處理，這裡只負責設定 daily_quota。
學生能不能看到機器人由 migrations/005 的 has_tool_permission() 判斷。
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.auth import CurrentUser, get_current_user, require_staff
from database.client import get_supabase

router = APIRouter(prefix="/api/tool-requests", tags=["Tool Requests"])

# 台灣沒有日光節約時間，用固定 +8 即可（Windows 沒裝 tzdata 時 ZoneInfo 會失敗）
TAIPEI = timezone(timedelta(hours=8))

REQUEST_STATUSES = ("pending", "approved", "rejected", "expired")

REQUEST_FIELDS = "id, user_id, tool_id, status, daily_quota, start_date, expire_date, created_at"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def today_taipei() -> date:
    return datetime.now(TAIPEI).date()


def effective_status(row: dict, today: date) -> str:
    """approved 但到期日已過 → expired"""
    if row.get("status") != "approved" or not row.get("expire_date"):
        return row.get("status")
    return "expired" if date.fromisoformat(row["expire_date"]) < today else "approved"


def _tool_titles(supabase, tool_ids: set) -> dict:
    # tool_permissions.tool_id 沒有外鍵，不能用 select 帶出 ai_tools，另外查一次
    ids = [t for t in tool_ids if t]
    if not ids:
        return {}
    res = supabase.table("ai_tools").select("id, title").in_("id", ids).execute()
    return {t["id"]: t["title"] for t in res.data or []}


def _to_output(row: dict, titles: dict, today: date) -> dict:
    return {
        "id": row["id"],
        "tool_id": row.get("tool_id"),
        # tool_id 為 NULL 代表全部機器人（目前只能從後台設定）
        "tool_title": titles.get(row.get("tool_id")) if row.get("tool_id") else "全部機器人",
        "status": effective_status(row, today),
        "daily_quota": row.get("daily_quota"),
        "start_date": row.get("start_date"),
        "expire_date": row.get("expire_date"),
        "created_at": row.get("created_at"),
    }


# ──────────────────────────────────────────
# Request models
# ──────────────────────────────────────────

class CreateToolRequest(BaseModel):
    # 用 UUID 型別驗證：tool_id 會放進下方 .or_() 的篩選字串，不能讓任意文字進去
    tool_id: UUID


class ReviewToolRequest(BaseModel):
    action: Literal["approve", "reject"]
    # 以下只在 approve 時使用；不填則到期日不限、每日次數沿用原值（預設 10）
    expire_date: Optional[date] = None
    daily_quota: Optional[int] = Field(None, ge=1, le=1000)


# ──────────────────────────────────────────
# 學生端
# ──────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tool_request(body: CreateToolRequest, user: CurrentUser = Depends(get_current_user)):
    """申請使用某支機器人"""
    supabase = get_supabase()
    tool_id = str(body.tool_id)

    tool = supabase.table("ai_tools").select("id, title, status").eq("id", tool_id).limit(1).execute()
    if not tool.data or tool.data[0].get("status") != "published":
        raise _error(status.HTTP_404_NOT_FOUND, "TOOL_NOT_FOUND", "找不到這支機器人，或它尚未開放")

    # 已有待審核、或仍有效的核准（含「全部機器人」的核准）時，不能重複申請
    existing = (
        supabase.table("tool_permissions")
        .select("status, expire_date")
        .eq("user_id", user.id)
        .or_(f"tool_id.eq.{tool_id},tool_id.is.null")
        .in_("status", ["pending", "approved"])
        .execute()
    )
    today = today_taipei()
    current = {effective_status(row, today) for row in existing.data or []}
    if "pending" in current:
        raise _error(status.HTTP_409_CONFLICT, "ALREADY_PENDING", "已送出申請，請等待老師審核")
    if "approved" in current:
        raise _error(status.HTTP_409_CONFLICT, "ALREADY_APPROVED", "您已經可以使用這支機器人")

    res = (
        supabase.table("tool_permissions")
        .insert({"user_id": user.id, "tool_id": tool_id, "status": "pending"})
        .execute()
    )
    return {
        "data": _to_output(res.data[0], {tool_id: tool.data[0]["title"]}, today),
        "message": "已送出申請，請等待老師審核",
    }


@router.get("/mine")
async def list_my_tool_requests(user: CurrentUser = Depends(get_current_user)):
    """目前登入者的所有申請與狀態（新的在前）"""
    supabase = get_supabase()
    res = (
        supabase.table("tool_permissions")
        .select(REQUEST_FIELDS)
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    titles = _tool_titles(supabase, {r.get("tool_id") for r in rows})
    today = today_taipei()
    return {"data": [_to_output(r, titles, today) for r in rows]}


# ──────────────────────────────────────────
# 教師端
# ──────────────────────────────────────────

@router.get("")
async def list_tool_requests(
    request_status: Optional[str] = Query(None, alias="status", description="pending | approved | rejected | expired"),
    user: CurrentUser = Depends(require_staff),
):
    """所有申請；?status=pending 即為待審核清單"""
    if request_status is not None and request_status not in REQUEST_STATUSES:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_STATUS", f"status 只能是 {' / '.join(REQUEST_STATUSES)}")

    supabase = get_supabase()
    res = (
        supabase.table("tool_permissions")
        .select(f"{REQUEST_FIELDS}, profiles(full_name, email)")
        .order("created_at", desc=True)
        .execute()
    )
    rows = res.data or []
    titles = _tool_titles(supabase, {r.get("tool_id") for r in rows})
    today = today_taipei()

    requests = []
    for row in rows:
        item = _to_output(row, titles, today)
        # expired 是算出來的，篩選要在算完之後
        if request_status and item["status"] != request_status:
            continue
        profile = row.get("profiles") or {}
        item.update(user_id=row["user_id"], full_name=profile.get("full_name"), email=profile.get("email"))
        requests.append(item)
    return {"data": requests}


@router.patch("/{request_id}")
async def review_tool_request(
    request_id: UUID,
    body: ReviewToolRequest,
    user: CurrentUser = Depends(require_staff),
):
    """
    approve：核准（可從任何狀態，也用來延長期限或改每日次數）
    reject：拒絕待審核的申請，或撤銷已核准的使用權
    """
    supabase = get_supabase()
    existing = supabase.table("tool_permissions").select(REQUEST_FIELDS).eq("id", str(request_id)).limit(1).execute()
    if not existing.data:
        raise _error(status.HTTP_404_NOT_FOUND, "REQUEST_NOT_FOUND", "找不到這筆申請")

    today = today_taipei()
    current = effective_status(existing.data[0], today)

    if body.action == "approve":
        if body.expire_date is not None and body.expire_date < today:
            raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_EXPIRE_DATE", "到期日不能早於今天")
        # start_date 不改：沿用申請當天，避免和資料庫 CURRENT_DATE（UTC）差一天而暫時看不到機器人
        changes = {
            "status": "approved",
            "expire_date": body.expire_date.isoformat() if body.expire_date else None,
        }
        if body.daily_quota is not None:
            changes["daily_quota"] = body.daily_quota
        message = "已核准申請"
    else:
        if current not in ("pending", "approved"):
            raise _error(
                status.HTTP_409_CONFLICT,
                "INVALID_STATUS_TRANSITION",
                f"目前狀態是 {current}，不能執行 reject",
            )
        changes = {"status": "rejected"}
        message = "已拒絕申請" if current == "pending" else "已撤銷使用權"

    res = supabase.table("tool_permissions").update(changes).eq("id", str(request_id)).execute()
    row = res.data[0]
    titles = _tool_titles(supabase, {row.get("tool_id")})
    return {"data": _to_output(row, titles, today), "message": message}
