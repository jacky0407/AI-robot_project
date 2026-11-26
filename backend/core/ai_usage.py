"""
AI 用量與費用（#81，需求說明書 AI-001、AI-002、15.1）

記錄：每一次 AI 呼叫寫一列 ai_usage_logs（migration 023）——模型、token、估計費用、延遲、成功與否、
      用平台金鑰還是學生自備金鑰。記錄由 llm_utils.invoke_with_retry 與 embeddings 自動呼叫 record()，
      API 端點只要用 track_usage() 包住這次請求，告訴它是誰、哪門課、哪一關。
      沒有包 track_usage() 的呼叫（例如單元測試）不記錄。記錄失敗只寫 log，不影響評分。

預算：courses.monthly_ai_budget_usd（每門課每月）與 AUTONOMOUS_MONTHLY_BUDGET_USD（自主探索）。
      只算平台金鑰；學生自備金鑰的費用不算。用到 BUDGET_WARN_RATIO（預設 8 成）先提醒，
      用完就不能開始新的作答或聊天訊息（check_budget 回 429）；已開始的評分不中斷。
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timezone
from typing import Iterator, Optional

from fastapi import HTTPException, status

from core.access import TAIPEI
from core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class UsageContext:
    db: object
    user_id: Optional[str] = None
    course_id: Optional[str] = None
    module_id: Optional[str] = None
    step_id: Optional[str] = None
    tool_id: Optional[str] = None
    access_mode: Optional[str] = None
    key_source: str = "platform"
    extra: dict = field(default_factory=dict)


_ctx: ContextVar[Optional[UsageContext]] = ContextVar("ai_usage_context", default=None)


@contextmanager
def track_usage(ctx: Optional[UsageContext]) -> Iterator[None]:
    token = _ctx.set(ctx)
    try:
        yield
    finally:
        _ctx.reset(token)


@contextmanager
def usage_tool(tool_id: Optional[str]) -> Iterator[None]:
    """聊天室裡換成回答的那個機器人：這段期間的呼叫記在它名下"""
    ctx = _ctx.get()
    if ctx is None or not tool_id:
        yield
        return
    token = _ctx.set(replace(ctx, tool_id=tool_id))
    try:
        yield
    finally:
        _ctx.reset(token)


def _model_name(model) -> str:
    return str(model or "").removeprefix("models/")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = get_settings().model_prices.get(_model_name(model))
    if not prices:
        if model:
            logger.warning(f"價目表沒有模型 {model}，費用記 0（請更新 MODEL_PRICES）")
        return 0.0
    return (input_tokens * prices.get("input", 0) + output_tokens * prices.get("output", 0)) / 1_000_000


def tokens_from_response(response) -> tuple[int, int]:
    """LangChain AIMessage.usage_metadata → (輸入, 輸出)；沒有就 (0, 0)"""
    usage = getattr(response, "usage_metadata", None) or {}
    if not isinstance(usage, dict):
        usage = dict(usage) if hasattr(usage, "keys") else {}
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def record(feature: str, model, *, input_tokens: int = 0, output_tokens: int = 0,
           latency_ms: Optional[int] = None, success: bool = True, error: Optional[str] = None) -> None:
    ctx = _ctx.get()
    if ctx is None:
        return
    model = _model_name(model)
    row = {
        "user_id": ctx.user_id, "course_id": ctx.course_id, "module_id": ctx.module_id,
        "step_id": ctx.step_id, "tool_id": ctx.tool_id, "access_mode": ctx.access_mode,
        "key_source": ctx.key_source, "feature": (feature or "unknown").split(":")[0][:50],
        "model": model or None, "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cost_usd": round(estimate_cost(model, input_tokens, output_tokens), 6),
        "latency_ms": latency_ms, "success": success, "error": (error or None) and str(error)[:300],
    }
    try:
        ctx.db.table("ai_usage_logs").insert(row).execute()
    except Exception as e:  # noqa: BLE001 - 記錄失敗不能影響評分
        logger.warning(f"AI 用量記錄失敗（是否尚未執行 migration 023？）：{e}")


# ──────────────────────────────────────────
# 預算
# ──────────────────────────────────────────

def month_start_utc(now: Optional[datetime] = None) -> datetime:
    """台灣時間這個月 1 號 00:00，換成 UTC"""
    local = (now or datetime.now(timezone.utc)).astimezone(TAIPEI)
    return datetime.combine(local.date().replace(day=1), time(0), tzinfo=TAIPEI).astimezone(timezone.utc)


def _month_total(db, course_id: Optional[str], autonomous: bool) -> float:
    res = db.rpc("ai_usage_total", {
        "p_course_id": course_id, "p_since": month_start_utc().isoformat(), "p_autonomous": autonomous,
    }).execute()
    data = res.data
    if isinstance(data, list):
        data = data[0] if data else 0
    if isinstance(data, dict):
        data = next(iter(data.values()), 0)
    return float(data or 0)


def budget_status(db, *, course_id: Optional[str], access_mode: str) -> Optional[dict]:
    """
    這次請求適用的月預算狀態；不限預算、學生自備金鑰、教師端都回 None。
    {"scope": "course" | "autonomous", "used_usd", "budget_usd", "ratio", "level": "ok" | "warn" | "exceeded"}
    """
    if access_mode in ("self_key", "staff"):
        return None
    settings = get_settings()
    try:
        if access_mode == "permission":
            budget, scope = settings.autonomous_monthly_budget_usd, "autonomous"
            if not budget:
                return None
            used = _month_total(db, None, True)
        else:
            if not course_id:
                return None
            rows = db.table("courses").select("monthly_ai_budget_usd").eq("id", course_id).limit(1).execute().data or []
            budget, scope = (rows[0].get("monthly_ai_budget_usd") if rows else None), "course"
            if not budget:
                return None
            used = _month_total(db, course_id, False)
    except Exception as e:  # noqa: BLE001 - 查不到預算（例如還沒跑 023）時不擋學生
        logger.warning(f"讀取 AI 預算失敗，這次不檢查：{e}")
        return None
    budget = float(budget)
    ratio = used / budget if budget else 0
    level = "exceeded" if ratio >= 1 else ("warn" if ratio >= settings.budget_warn_ratio else "ok")
    return {"scope": scope, "used_usd": round(used, 4), "budget_usd": budget, "ratio": round(ratio, 3), "level": level}


def check_budget(db, *, course_id: Optional[str], access_mode: str) -> Optional[dict]:
    """開始新的作答或聊天訊息前呼叫：預算用完回 429 BUDGET_EXCEEDED；否則回 budget_status（可能是 warn）"""
    st = budget_status(db, course_id=course_id, access_mode=access_mode)
    if st and st["level"] == "exceeded":
        who = "這門課" if st["scope"] == "course" else "自主探索"
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail={
            "code": "BUDGET_EXCEEDED",
            "message": f"{who}本月的 AI 額度已用完，請聯絡老師",
            "budget": st,
        })
    return st


def course_for_access(db, user_id: str, module_id: Optional[str], access) -> Optional[str]:
    """這次作答算在哪門課：課程模式從成員資料拿；拿不到時找模組被指派、且學生是 active 成員的課程"""
    member = getattr(access, "member", None) or {}
    if member.get("course_id"):
        return member["course_id"]
    if getattr(access, "mode", None) not in ("course", "self_key") or not module_id:
        return None
    try:
        from core.materials import courses_for_module

        courses = courses_for_module(db, user_id, module_id)
        return courses[0] if courses else None
    except Exception:  # noqa: BLE001
        return None
