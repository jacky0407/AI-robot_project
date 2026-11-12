"""
資料匯出 API（DAT-001、開會紀錄 #49 第 7 項；docs/api.md 第七節）

  POST /api/export/research   研究用：去識別化（姓名、Email 換成匿名研究編號），只含已同意研究使用的學生
  POST /api/export/teaching   教學用：含姓名與 Email，供老師教學管理

兩者都只限教授（owner），回傳 UTF-8（含 BOM，Excel 開中文不亂碼）的 CSV 檔，
並在 export_logs 記一筆稽核紀錄（migration 018；寫入失敗只記 log，不擋匯出）。

一列 = 一次作答：作答者、步驟、AI 評分（逐構面）、錯誤分類、提示使用、教師最終判定。
作答原文預設不匯出（include_content），研究用時就算勾選也會再過一次個資遮罩。
"""

import csv
import io
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel

from core.auth import CurrentUser, require_staff
from core.privacy import PII_PATTERNS
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["Export"])

ATTEMPT_SELECT = (
    "id, user_id, module_id, step_id, attempt_number, status, tutor_action, created_at, user_input_content, "
    "module_steps(step_order, step_title, pass_score, ai_tools(title)), "
    "ai_evaluations(total_score, confidence, dimension_scores, detected_errors), "
    "teacher_reviews(decision, final_score, is_published, reviewed_at), "
    "prompt_logs(hint_level, hint_trigger)"
)

COMMON_COLUMNS = [
    "module_title", "step_order", "step_title", "tool_title", "attempt_number", "submitted_at",
    "status", "tutor_action", "answer_chars", "ai_total_score", "ai_confidence", "ai_dimensions",
    "detected_errors", "hints_used", "hint_levels", "teacher_decision", "teacher_final_score",
    "teacher_published",
]


class ExportRequest(BaseModel):
    course_id: UUID
    module_id: Optional[UUID] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    include_content: bool = False
    format: Literal["csv"] = "csv"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _require_owner(user: CurrentUser) -> None:
    if user.role != "owner":
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有教授可以匯出資料")


def _as_list(value) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return [value] if isinstance(value, dict) else []


def _first(value) -> dict:
    items = _as_list(value)
    return items[0] if items else {}


def mask_pii(text: str) -> str:
    """研究匯出的作答原文：符合個資規則（core/privacy.PII_PATTERNS，與送出作答前的檢查同一組）的片段換成 [已遮罩]"""
    masked = text or ""
    for _, pattern in PII_PATTERNS:
        masked = re.sub(pattern, "[已遮罩]", masked)
    return masked


def _row(attempt: dict, modules: dict[str, str]) -> dict:
    step = _first(attempt.get("module_steps"))
    evaluation = _first(attempt.get("ai_evaluations"))
    review = (_as_list(attempt.get("teacher_reviews")) or [{}])[-1]
    hints = sorted(_as_list(attempt.get("prompt_logs")), key=lambda h: h.get("hint_level") or 0)
    dims = ";".join(
        f"{d.get('dimension')}:{d.get('score')}/{d.get('max_score')}"
        for d in evaluation.get("dimension_scores") or [] if isinstance(d, dict)
    )
    errors = ";".join(
        str(e.get("code") or e.get("label")) for e in evaluation.get("detected_errors") or [] if isinstance(e, dict)
    )
    return {
        "module_title": modules.get(attempt.get("module_id"), ""),
        "step_order": step.get("step_order"),
        "step_title": step.get("step_title") or "",
        "tool_title": _first(step.get("ai_tools")).get("title") or "",
        "attempt_number": attempt.get("attempt_number"),
        "submitted_at": attempt.get("created_at"),
        "status": attempt.get("status"),
        "tutor_action": attempt.get("tutor_action") or "",
        "answer_chars": len(attempt.get("user_input_content") or ""),
        "ai_total_score": evaluation.get("total_score"),
        "ai_confidence": evaluation.get("confidence"),
        "ai_dimensions": dims,
        "detected_errors": errors,
        "hints_used": len(hints),
        "hint_levels": ";".join(str(h.get("hint_level")) for h in hints),
        "teacher_decision": review.get("decision") or "",
        "teacher_final_score": review.get("final_score"),
        "teacher_published": review.get("is_published") if review else "",
    }


def _load(db, body: ExportRequest) -> tuple[dict, list[dict], list[dict], dict[str, str]]:
    """課程、成員、作答、模組名稱"""
    course_id = str(body.course_id)
    course = db.table("courses").select("id, title").eq("id", course_id).limit(1).execute().data or []
    if not course:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")

    assigned = (
        db.table("course_modules").select("module_id, learning_modules(title)").eq("course_id", course_id).execute()
    ).data or []
    modules = {a["module_id"]: _first(a.get("learning_modules")).get("title") or "" for a in assigned}
    if body.module_id:
        modules = {k: v for k, v in modules.items() if k == str(body.module_id)}
        if not modules:
            raise _error(status.HTTP_404_NOT_FOUND, "MODULE_NOT_IN_COURSE", "這個模組沒有指派給這門課程")

    members = (
        db.table("course_members").select("user_id, status, profiles(full_name, email, role)")
        .eq("course_id", course_id).execute()
    ).data or []
    # 只匯出學生；被移出的學生先前的作答仍屬於這門課的學習歷程
    members = [m for m in members if _first(m.get("profiles")).get("role") not in ("owner", "assistant")
               and m.get("status") in ("active", "removed")]
    user_ids = [m["user_id"] for m in members]

    attempts: list[dict] = []
    if user_ids and modules:
        attempts = (
            db.table("step_attempts").select(ATTEMPT_SELECT)
            .in_("module_id", list(modules)).in_("user_id", user_ids).execute()
        ).data or []
    if body.date_from:
        attempts = [a for a in attempts if (a.get("created_at") or "")[:10] >= body.date_from.isoformat()]
    if body.date_to:
        attempts = [a for a in attempts if (a.get("created_at") or "")[:10] <= body.date_to.isoformat()]
    attempts.sort(key=lambda a: (a.get("user_id") or "", a.get("created_at") or ""))
    return course[0], members, attempts, modules


def _csv(columns: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _audit(db, user: CurrentUser, kind: str, body: ExportRequest, row_count: int) -> None:
    try:
        db.table("export_logs").insert({
            "exported_by": user.id,
            "kind": kind,
            "course_id": str(body.course_id),
            "params": json.loads(body.model_dump_json()),
            "row_count": row_count,
        }).execute()
    except Exception as e:  # noqa: BLE001 - 稽核寫入失敗不擋匯出，但要留下紀錄
        logger.warning(f"匯出稽核紀錄寫入失敗（請確認已執行 migration 018）：{e}")


def _download(content: bytes, kind: str, row_count: int) -> Response:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{kind}-export-{stamp}.csv"',
            "X-Export-Row-Count": str(row_count),
            "Access-Control-Expose-Headers": "Content-Disposition, X-Export-Row-Count",
        },
    )


@router.post("/research")
async def export_research(body: ExportRequest, user: CurrentUser = Depends(require_staff)):
    """去識別化：匿名研究編號取代姓名與 Email，只含 research_consents.is_consented 且未撤回的學生"""
    _require_owner(user)
    db = get_supabase()
    _, members, attempts, modules = _load(db, body)

    user_ids = [m["user_id"] for m in members]
    consents = (
        db.table("research_consents").select("user_id, anonymous_research_id, is_consented, withdrawn_at")
        .in_("user_id", user_ids).execute()
    ).data if user_ids else []
    anon = {
        c["user_id"]: c["anonymous_research_id"]
        for c in consents or []
        if c.get("is_consented") and not c.get("withdrawn_at") and c.get("anonymous_research_id")
    }

    rows = []
    for a in attempts:
        rid = anon.get(a.get("user_id"))
        if not rid:
            continue  # 沒同意或已撤回：不匯出
        row = {"research_id": rid, **_row(a, modules)}
        if body.include_content:
            row["answer_text"] = mask_pii(a.get("user_input_content") or "")
        rows.append(row)

    columns = ["research_id", *COMMON_COLUMNS] + (["answer_text"] if body.include_content else [])
    _audit(db, user, "research", body, len(rows))
    return _download(_csv(columns, rows), "research", len(rows))


@router.post("/teaching")
async def export_teaching(body: ExportRequest, user: CurrentUser = Depends(require_staff)):
    """教學用：含學生姓名與 Email（可識別），只供老師教學管理，不可用於研究發表"""
    _require_owner(user)
    db = get_supabase()
    _, members, attempts, modules = _load(db, body)
    people = {m["user_id"]: _first(m.get("profiles")) for m in members}

    rows = []
    for a in attempts:
        p = people.get(a.get("user_id")) or {}
        row = {"student_name": p.get("full_name") or "", "student_email": p.get("email") or "", **_row(a, modules)}
        if body.include_content:
            row["answer_text"] = a.get("user_input_content") or ""
        rows.append(row)

    columns = ["student_name", "student_email", *COMMON_COLUMNS] + (["answer_text"] if body.include_content else [])
    _audit(db, user, "teaching", body, len(rows))
    return _download(_csv(columns, rows), "teaching", len(rows))
