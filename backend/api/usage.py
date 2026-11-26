"""
AI 用量與費用（#81）— 限教師端

  GET /api/courses/{course_id}/ai-usage?month=YYYY-MM   這門課某個月的用量（預設本月，台灣時間）

每月預算在 PATCH /api/courses/{course_id} 設定（monthly_ai_budget_usd，空白 = 不限）。
費用是依價目表（core/config.py 的 MODEL_PRICES）估算，實際金額以 Google Cloud 帳單為準。
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, time, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.access import TAIPEI
from core.ai_usage import budget_status
from core.auth import CurrentUser, require_staff
from core.chat_room import migration_missing
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/courses", tags=["usage"])

FEATURE_LABELS = {
    "score_dimension": "逐項評分",
    "synthesize": "整體回饋",
    "feedback_only": "不評分的回饋",
    "detect_errors": "錯誤偵測",
    "generate_hint": "分層提示",
    "personalize_hint": "分層提示",
    "coherence": "跨步驟檢核",
    "chat_router": "聊天室調度",
    "chat_bot": "聊天回覆",
    "embedding": "教材與助教搜尋",
}


def _month_range(month: Optional[str]) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc).astimezone(TAIPEI)
    if month:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(status_code=400, detail={"code": "INVALID_MONTH", "message": "月份格式是 YYYY-MM"})
        year, mon = int(month[:4]), int(month[5:])
    else:
        year, mon = now.year, now.month
    start = datetime.combine(datetime(year, mon, 1).date(), time(0), tzinfo=TAIPEI)
    end = datetime.combine(datetime(year + (mon == 12), mon % 12 + 1, 1).date(), time(0), tzinfo=TAIPEI)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), f"{year:04d}-{mon:02d}"


def _sum_into(bucket: dict, row: dict) -> None:
    bucket["calls"] += int(row.get("calls") or 0)
    bucket["failed"] += int(row.get("failed") or 0)
    bucket["input_tokens"] += int(row.get("input_tokens") or 0)
    bucket["output_tokens"] += int(row.get("output_tokens") or 0)
    cost = float(row.get("cost_usd") or 0)
    bucket["cost_usd"] += cost
    if row.get("key_source") == "student":
        bucket["student_key_cost_usd"] += cost
    else:
        bucket["platform_cost_usd"] += cost


def _empty() -> dict:
    return {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "platform_cost_usd": 0.0, "student_key_cost_usd": 0.0}


def _rounded(bucket: dict) -> dict:
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in bucket.items()}


@router.get("/{course_id}/ai-usage")
async def course_ai_usage(
    course_id: UUID,
    month: Optional[str] = Query(None, description="YYYY-MM，預設本月（台灣時間）"),
    user: CurrentUser = Depends(require_staff),
):
    db = get_supabase()
    course_id = str(course_id)
    course = db.table("courses").select("id, title, monthly_ai_budget_usd").eq("id", course_id).limit(1).execute().data
    if not course:
        raise HTTPException(status_code=404, detail={"code": "COURSE_NOT_FOUND", "message": "找不到這門課程"})
    since, until, label = _month_range(month)

    try:
        rows = db.rpc("ai_usage_summary", {
            "p_course_id": course_id, "p_since": since.isoformat(), "p_until": until.isoformat(),
        }).execute().data or []
    except Exception as e:  # noqa: BLE001
        if migration_missing(e) or "ai_usage_summary" in str(e):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
                "code": "MIGRATION_REQUIRED",
                "message": "資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/023_ai_usage.sql",
            })
        raise

    total = _empty()
    by_feature: dict = defaultdict(_empty)
    by_model: dict = defaultdict(_empty)
    by_tool: dict = defaultdict(_empty)
    by_day: dict = defaultdict(_empty)
    for r in rows:
        _sum_into(total, r)
        _sum_into(by_feature[FEATURE_LABELS.get(r.get("feature"), r.get("feature") or "其他")], r)
        _sum_into(by_model[r.get("model") or "（未知）"], r)
        _sum_into(by_tool[r.get("tool_id") or ""], r)
        _sum_into(by_day[str(r.get("day"))], r)

    tool_ids = [t for t in by_tool if t]
    titles = {}
    if tool_ids:
        titles = {t["id"]: t.get("title") for t in
                  db.table("ai_tools").select("id, title").in_("id", tool_ids).execute().data or []}

    def ranked(groups: dict, name_of=lambda k: k) -> list[dict]:
        items = [{"name": name_of(k), **_rounded(v)} for k, v in groups.items()]
        return sorted(items, key=lambda i: i["cost_usd"], reverse=True)

    return {"data": {
        "course_id": course_id,
        "month": label,
        "budget": budget_status(db, course_id=course_id, access_mode="course") if label == _month_range(None)[2] else None,
        "monthly_budget_usd": course[0].get("monthly_ai_budget_usd"),
        "total": _rounded(total),
        "by_feature": ranked(by_feature),
        "by_tool": ranked(by_tool, lambda k: titles.get(k) or ("（教材與其他）" if not k else "（已刪除的機器人）")),
        "by_model": ranked(by_model),
        "by_day": sorted(({"day": k, **_rounded(v)} for k, v in by_day.items()), key=lambda d: d["day"]),
        "note": "費用依價目表估算，實際金額以 Google Cloud 帳單為準；學生自備金鑰的費用不算進課程預算",
    }}
