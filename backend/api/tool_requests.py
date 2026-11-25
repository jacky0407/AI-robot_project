"""
自主探索的申請 API：學生申請使用某個「模組」，老師核准時設定到期日與每日次數（#86，migration 025）

核准後，學生可以做這個模組裡所有已發布、啟用中的關卡（core/access.py、migration 025 的 RLS）。
聊天室關卡照舊：每則訊息扣一次每日次數（api/chat.py）。

資料存在 tool_permissions（一筆 = 一次申請）：
    pending  → 等老師審核      approved → 已核准（可用到 expire_date）
    rejected → 老師拒絕或撤銷  expired  → 已過期（見下方說明）

申請的對象：
    module_id 有值              → 申請模組（新的申請一律是這種）
    tool_id 有值                → 舊的「申請機器人」，照常有效到原本的到期日；不能再新增這種申請
    tool_id、module_id 都是 NULL → 全部機器人（只能從後台設定）

「已過期」不靠排程改資料：status 為 approved 但 expire_date 已過（台灣時間），
API 一律回傳 expired。被拒絕或過期後可以再申請，會新增一筆，保留歷史。

額度（used_today 扣次數、每天歸零）由 practice / chat API 處理，這裡只負責設定 daily_quota。
API 路徑沿用 /api/tool-requests（前端與文件都用這個），內容改成模組。
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.audit import record
from core.auth import CurrentUser, get_current_user, require_staff
from database.client import get_supabase

router = APIRouter(prefix="/api/tool-requests", tags=["Module Requests"])

# 台灣沒有日光節約時間，用固定 +8 即可（Windows 沒裝 tzdata 時 ZoneInfo 會失敗）
TAIPEI = timezone(timedelta(hours=8))

REQUEST_STATUSES = ("pending", "approved", "rejected", "expired")

# 給學生看的模組公開資訊；不要加入機器人的 system_prompt、rubric_criteria 等教學內容
MODULE_CATALOG_FIELDS = "id, title, description, domain, created_at"

REQUEST_FIELDS = "id, user_id, tool_id, module_id, status, daily_quota, start_date, expire_date, created_at"

UNDEFINED_COLUMN = "42703"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _execute(query):
    """還沒跑 migration 025（沒有 module_id 欄位）時回 503，說明要跑哪一支"""
    try:
        return query.execute()
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) == UNDEFINED_COLUMN:
            raise _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "MIGRATION_REQUIRED",
                "資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/025_module_requests.sql",
            )
        raise


def today_taipei() -> date:
    return datetime.now(TAIPEI).date()


def effective_status(row: dict, today: date) -> str:
    """approved 但到期日已過 → expired"""
    if row.get("status") != "approved" or not row.get("expire_date"):
        return row.get("status")
    return "expired" if date.fromisoformat(row["expire_date"]) < today else "approved"


def request_kind(row: dict) -> str:
    """module = 申請模組；tool = 舊的申請機器人；all = 全部機器人"""
    if row.get("module_id"):
        return "module"
    if row.get("tool_id"):
        return "tool"
    return "all"


def _titles(supabase, rows: list[dict]) -> tuple[dict, dict]:
    """(模組名稱, 機器人名稱)；tool_permissions 的 tool_id 沒有外鍵，不能用 select 帶出，另外查"""
    module_ids = sorted({r["module_id"] for r in rows if r.get("module_id")})
    tool_ids = sorted({r["tool_id"] for r in rows if r.get("tool_id")})
    modules, tools = {}, {}
    if module_ids:
        res = supabase.table("learning_modules").select("id, title").in_("id", module_ids).execute()
        modules = {m["id"]: m["title"] for m in res.data or []}
    if tool_ids:
        res = supabase.table("ai_tools").select("id, title").in_("id", tool_ids).execute()
        tools = {t["id"]: t["title"] for t in res.data or []}
    return modules, tools


def _to_output(row: dict, titles: tuple[dict, dict], today: date) -> dict:
    modules, tools = titles
    kind = request_kind(row)
    if kind == "module":
        title = modules.get(row["module_id"]) or "（已刪除的模組）"
    elif kind == "tool":
        title = tools.get(row["tool_id"]) or "（已刪除的機器人）"
    else:
        title = "全部機器人"
    return {
        "id": row["id"],
        "kind": kind,
        "module_id": row.get("module_id"),
        "tool_id": row.get("tool_id"),
        "title": title,
        "status": effective_status(row, today),
        "daily_quota": row.get("daily_quota"),
        "start_date": row.get("start_date"),
        "expire_date": row.get("expire_date"),
        "created_at": row.get("created_at"),
    }


def _active_step_counts(supabase, module_ids: list[str]) -> dict[str, int]:
    """每個模組啟用中、機器人已發布的關卡數（學生核准後真的能做的關卡）"""
    if not module_ids:
        return {}
    res = (
        supabase.table("module_steps")
        .select("module_id, is_active, ai_tools(status)")
        .in_("module_id", module_ids)
        .execute()
    )
    counts: dict[str, int] = {}
    for s in res.data or []:
        if s.get("is_active", True) and (s.get("ai_tools") or {}).get("status") == "published":
            counts[s["module_id"]] = counts.get(s["module_id"], 0) + 1
    return counts


# ──────────────────────────────────────────
# Request models
# ──────────────────────────────────────────

class CreateModuleRequest(BaseModel):
    # 用 UUID 型別驗證：module_id 會放進下方 .or_() 的篩選字串，不能讓任意文字進去
    module_id: UUID


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    # 以下只在 approve 時使用，不填都沿用原值：
    #   expire_date 沒傳 → 保留原本的到期日；傳 null → 改成不限期
    #   daily_quota 沒傳 → 保留原值（資料表預設 10）
    expire_date: Optional[date] = None
    daily_quota: Optional[int] = Field(None, ge=1, le=1000)


# ──────────────────────────────────────────
# 學生端
# ──────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_module_request(body: CreateModuleRequest, user: CurrentUser = Depends(get_current_user)):
    """申請使用某個模組"""
    supabase = get_supabase()
    module_id = str(body.module_id)

    module = supabase.table("learning_modules").select("id, title, is_published").eq("id", module_id).limit(1).execute()
    if not module.data or not module.data[0].get("is_published"):
        raise _error(status.HTTP_404_NOT_FOUND, "MODULE_NOT_FOUND", "找不到這個模組，或它尚未開放")
    if not _active_step_counts(supabase, [module_id]).get(module_id):
        raise _error(status.HTTP_400_BAD_REQUEST, "MODULE_EMPTY", "這個模組目前沒有可以練習的關卡")

    # 已有待審核、或仍有效的核准（這個模組，或全部機器人）時，不能重複申請
    existing = _execute(
        supabase.table("tool_permissions")
        .select("status, expire_date, tool_id, module_id")
        .eq("user_id", user.id)
        .or_(f"module_id.eq.{module_id},and(tool_id.is.null,module_id.is.null)")
        .in_("status", ["pending", "approved"])
    )
    today = today_taipei()
    current = {effective_status(row, today) for row in existing.data or []}
    if "pending" in current:
        raise _error(status.HTTP_409_CONFLICT, "ALREADY_PENDING", "已送出申請，請等待老師審核")
    if "approved" in current:
        raise _error(status.HTTP_409_CONFLICT, "ALREADY_APPROVED", "您已經可以使用這個模組")

    res = _execute(
        supabase.table("tool_permissions")
        .insert({"user_id": user.id, "module_id": module_id, "status": "pending"})
    )
    return {
        "data": _to_output(res.data[0], ({module_id: module.data[0]["title"]}, {}), today),
        "message": "已送出申請，請等待老師審核",
    }


@router.get("/modules")
async def list_requestable_modules(user: CurrentUser = Depends(get_current_user)):
    """
    可以申請的模組：已發布、至少有一個可以練習的關卡。

    學生在被核准前讀不到模組（migration 005 / 025 的 RLS），所以由後端列出。
    只回傳名稱、說明、領域與關卡數，不含機器人的教學內容。
    """
    supabase = get_supabase()
    res = (
        supabase.table("learning_modules")
        .select(MODULE_CATALOG_FIELDS)
        .eq("is_published", True)
        .order("created_at", desc=False)
        .execute()
    )
    modules = res.data or []
    counts = _active_step_counts(supabase, [m["id"] for m in modules])
    return {"data": [
        {**m, "step_count": counts[m["id"]]}
        for m in modules
        if counts.get(m["id"])
    ]}


@router.get("/mine")
async def list_my_requests(user: CurrentUser = Depends(get_current_user)):
    """目前登入者的所有申請與狀態（新的在前；含舊的機器人申請）"""
    supabase = get_supabase()
    res = _execute(
        supabase.table("tool_permissions")
        .select(REQUEST_FIELDS)
        .eq("user_id", user.id)
        .order("created_at", desc=True)
    )
    rows = res.data or []
    titles = _titles(supabase, rows)
    today = today_taipei()
    return {"data": [_to_output(r, titles, today) for r in rows]}


# ──────────────────────────────────────────
# 教師端
# ──────────────────────────────────────────

@router.get("")
async def list_requests(
    request_status: Optional[str] = Query(None, alias="status", description="pending | approved | rejected | expired"),
    user: CurrentUser = Depends(require_staff),
):
    """所有申請；?status=pending 即為待審核清單"""
    if request_status is not None and request_status not in REQUEST_STATUSES:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_STATUS", f"status 只能是 {' / '.join(REQUEST_STATUSES)}")

    supabase = get_supabase()
    res = _execute(
        supabase.table("tool_permissions")
        .select(f"{REQUEST_FIELDS}, profiles(full_name, email)")
        .order("created_at", desc=True)
    )
    rows = res.data or []
    titles = _titles(supabase, rows)
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
async def review_request(
    request_id: UUID,
    body: ReviewRequest,
    user: CurrentUser = Depends(require_staff),
):
    """
    approve：核准（可從任何狀態，也用來延長期限或改每日次數；舊的機器人申請也可以延長）
    reject：拒絕待審核的申請，或撤銷已核准的使用權
    """
    supabase = get_supabase()
    existing = _execute(supabase.table("tool_permissions").select(REQUEST_FIELDS).eq("id", str(request_id)).limit(1))
    if not existing.data:
        raise _error(status.HTTP_404_NOT_FOUND, "REQUEST_NOT_FOUND", "找不到這筆申請")

    today = today_taipei()
    current = effective_status(existing.data[0], today)

    if body.action == "approve":
        # start_date 不改：沿用申請當天，避免和資料庫 CURRENT_DATE（UTC）差一天而暫時看不到模組
        changes = {"status": "approved"}

        # 「沒傳」和「傳 null」要分開：只改每日次數時不能順便把到期日清成不限期
        if "expire_date" in body.model_fields_set:
            if body.expire_date is not None and body.expire_date < today:
                raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_EXPIRE_DATE", "到期日不能早於今天")
            changes["expire_date"] = body.expire_date.isoformat() if body.expire_date else None
        elif current == "expired":
            # 保留舊的到期日，核准完還是過期，老師會以為沒成功
            raise _error(status.HTTP_400_BAD_REQUEST, "EXPIRE_DATE_REQUIRED", "這筆申請已過期，請設定新的到期日")

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
    output = _to_output(row, _titles(supabase, [row]), today)
    record(supabase, user, f"permission.{body.action}", target_type="permission", target_id=request_id, detail={
        "student_id": row.get("user_id"),
        "kind": output["kind"],
        "module_id": row.get("module_id"),
        "tool_id": row.get("tool_id"),
        "from_status": current,
        "changes": changes,
    })
    return {"data": output, "message": message}
