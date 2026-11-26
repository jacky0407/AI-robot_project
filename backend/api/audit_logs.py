"""
操作紀錄查詢（#90，migration 026）：只限教授（owner）

GET /api/admin/audit-logs
    ?action=tool.update     只看某一種操作；也可以只給前綴，例如 action=tool. 看機器人的全部操作
    &actor_id=<uuid>        只看某位老師或助理
    &date_from=2026-11-01   台灣時間的日期（含當天）
    &date_to=2026-11-30     台灣時間的日期（含當天）
    &limit=50&offset=0      分頁（limit 最多 200）

紀錄只能新增，這裡沒有修改或刪除的 API（資料庫也用觸發器擋住）。寫入在 core/audit.py。
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.audit import ACTIONS
from core.auth import CurrentUser, require_owner
from database.client import get_supabase

router = APIRouter(prefix="/api/admin/audit-logs", tags=["Audit Logs"])

TAIPEI = timezone(timedelta(hours=8))
MISSING_TABLE_CODES = ("42P01", "PGRST205")
FIELDS = "id, actor_id, actor_role, action, target_type, target_id, detail, created_at"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def taipei_day_start_utc(day: date) -> str:
    """台灣時間某天 00:00 換成 UTC 的 ISO 字串（資料庫存 UTC）"""
    return datetime.combine(day, time.min, tzinfo=TAIPEI).astimezone(timezone.utc).isoformat()


@router.get("")
async def list_audit_logs(
    action: Optional[str] = Query(None, max_length=50),
    actor_id: Optional[UUID] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100000),
    user: CurrentUser = Depends(require_owner),
):
    """操作紀錄（新的在前），每筆附操作者姓名與操作的中文說明"""
    if date_from and date_to and date_from > date_to:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "開始日期不能晚於結束日期")

    db = get_supabase()
    query = db.table("audit_logs").select(FIELDS)
    if action:
        # 結尾是「.」代表前綴（某類對象的全部操作），否則完全相同
        query = query.like("action", f"{action}%") if action.endswith(".") else query.eq("action", action)
    if actor_id:
        query = query.eq("actor_id", str(actor_id))
    if date_from:
        query = query.gte("created_at", taipei_day_start_utc(date_from))
    if date_to:
        query = query.lt("created_at", taipei_day_start_utc(date_to + timedelta(days=1)))

    try:
        # 多拿一筆判斷還有沒有下一頁
        res = query.order("created_at", desc=True).range(offset, offset + limit).execute()
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) in MISSING_TABLE_CODES:
            raise _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "MIGRATION_REQUIRED",
                "資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/026_audit_logs.sql",
            )
        raise

    rows = res.data or []
    has_more = len(rows) > limit
    rows = rows[:limit]

    actor_ids = sorted({r["actor_id"] for r in rows if r.get("actor_id")})
    people = {}
    if actor_ids:
        profiles = db.table("profiles").select("id, full_name, email").in_("id", actor_ids).execute()
        people = {p["id"]: p for p in profiles.data or []}

    data = []
    for r in rows:
        person = people.get(r.get("actor_id")) or {}
        data.append({
            **r,
            "action_label": ACTIONS.get(r.get("action"), r.get("action")),
            # 帳號已刪除時顯示（已刪除的帳號），紀錄本身不會被改
            "actor_name": person.get("full_name") or person.get("email") or "（已刪除的帳號）",
            "actor_email": person.get("email"),
        })
    return {
        "data": data,
        "has_more": has_more,
        "actions": [{"value": k, "label": v} for k, v in ACTIONS.items()],
    }
