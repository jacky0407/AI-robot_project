"""
教授監控總覽 API（TEAM_ROLES.md：學生對答監控後台）

  GET /api/admin/overview?course_id=…[&module_id=…]
      一門課「所有學生 × 所有步驟」的作答狀況總表，外加每一步與每位學生的摘要
  GET /api/admin/students/{user_id}/steps/{step_id}
      點進表格的一格：這位學生在這一步的每一次作答（AI 分數、提示數、教師判定）
  GET /api/admin/students/{user_id}/profile?module_id=…
      點學生名字：這位學生在這個模組的學習者側寫（熟練度、常犯／已改善錯誤、教師叮嚀）

為什麼是「學生 × 步驟」而不是 api.md 原本的「依 Session 列出」：
  前端每次打開練習頁都會產生新的 session_id（KNOWN_GAPS #3，刻意設計），
  同一位學生在同一步的作答會散在好幾個 Session，依 Session 列出教授反而看不出進度。

每一格的兩件事分開看（2026-11-12 排練回饋：混成一個狀態時「已通過」會被「等老師」蓋掉）：
  progress            not_started（沒作答）／not_passed（有作答、沒通過）／passed（通過；已發布的教師判定優先，
                      規則同 core/step_unlock.attempt_passed）
  needs_review_count  有幾筆作答在等老師（被轉介、AI 信心不足，或這一步是教師強制審核點），且還沒有教師判定
  score／score_source 有已發布的教師分數就用老師的，否則用 AI 最新分數

舊欄位 status（not_started / in_progress / needs_review / passed）保留給舊前端，新畫面請用上面三個。

權限：限教師端（owner / assistant）。只讀，不寫任何資料。
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.auth import STAFF_ROLES, CurrentUser, require_staff
from core.learner_profile import ATTEMPT_SELECT as PROFILE_ATTEMPT_SELECT
from core.learner_profile import build_profile, load_latest_profile
from core.step_unlock import DEFAULT_PASS_SCORE, attempt_passed
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

STEP_SELECT = (
    "id, module_id, step_order, step_title, is_required, is_active, pass_score, "
    "require_teacher_review, tool_id, ai_tools(title)"
)
# 還沒跑 migration 010（沒有 is_active）時用這個，視為全部啟用
STEP_SELECT_BEFORE_010 = STEP_SELECT.replace(" is_active,", "")

ATTEMPT_SELECT = (
    "id, user_id, step_id, attempt_number, status, tutor_action, created_at, "
    "ai_evaluations(total_score, needs_teacher_review), "
    "teacher_reviews(final_score, is_published, decision, reviewed_at), "
    "prompt_logs(id)"
)
# 還沒跑 migration 003（沒有 tutor_action）時用這個，看不到被轉介的作答
ATTEMPT_SELECT_BEFORE_003 = ATTEMPT_SELECT.replace(" tutor_action,", "")

DETAIL_SELECT = (
    "id, attempt_number, status, tutor_action, created_at, user_input_content, "
    "ai_evaluations(total_score, dimension_scores, feedback_text, confidence, needs_teacher_review, detected_errors), "
    "teacher_reviews(decision, final_score, final_feedback, is_published, reviewed_at), "
    "prompt_logs(hint_level, hint_trigger, hint_source, created_at)"
)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _as_list(value) -> list:
    """PostgREST 的巢狀關聯可能回傳 list、單一 dict 或 None，統一成 list。"""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _select_with_fallback(run, columns: str, fallback: str, label: str):
    """欄位還不存在（migration 沒跑）時退回舊查詢，總覽照常顯示。"""
    try:
        return run(columns)
    except Exception as e:  # noqa: BLE001 - postgrest 例外型別依版本不同
        logger.warning(f"{label} 查詢失敗，改用舊欄位（請確認 migrations 已執行）：{e}")
        return run(fallback)


# ──────────────────────────────────────────
# 計算（純函式，方便單元測試）
# ──────────────────────────────────────────

def review_reasons(attempt: dict, step: dict) -> list[str]:
    """這次作答為什麼需要老師看；已有教師判定的作答回傳 []。與 /api/review/pending 的理由一致。"""
    if _as_list(attempt.get("teacher_reviews")):
        return []
    evaluations = _as_list(attempt.get("ai_evaluations"))
    escalated = attempt.get("tutor_action") == "escalate"
    if not (evaluations or escalated):
        return []  # 只拿到提示、還沒評分的作答，老師不需要看

    reasons = []
    if escalated:
        reasons.append("escalated")
    if any(e.get("needs_teacher_review") for e in evaluations):
        reasons.append("low_confidence")
    if step.get("require_teacher_review"):
        reasons.append("step_requires_review")
    return reasons


def summarize_cell(attempts: list[dict], step: dict) -> dict:
    """一位學生在一個步驟的狀況。attempts = 這位學生在這一步的所有作答（順序不拘）。"""
    if not attempts:
        return {
            "status": "not_started", "progress": "not_started", "score": None, "score_source": None,
            "attempt_count": 0, "evaluated_count": 0,
            "latest_ai_score": None, "best_ai_score": None, "teacher_score": None,
            "passed": False, "hints_used": 0, "needs_review_count": 0,
            "review_reasons": [], "last_activity_at": None,
        }

    ordered = sorted(attempts, key=lambda a: (a.get("attempt_number") or 0, a.get("created_at") or ""))
    pass_score = step.get("pass_score") or DEFAULT_PASS_SCORE

    scores = []  # 依作答順序，有 AI 評分的才算
    for a in ordered:
        for e in _as_list(a.get("ai_evaluations")):
            if e.get("total_score") is not None:
                scores.append(e["total_score"])

    teacher_score = None
    for a in ordered:  # 最後一次「已發布、有給分」的教師判定
        for r in _as_list(a.get("teacher_reviews")):
            if r.get("is_published") and r.get("final_score") is not None:
                teacher_score = r["final_score"]

    reasons: list[str] = []
    needs_review_count = 0
    for a in ordered:
        rs = review_reasons(a, step)
        if rs:
            needs_review_count += 1
            reasons.extend(r for r in rs if r not in reasons)

    passed = any(attempt_passed(a, pass_score) for a in ordered)
    if needs_review_count:
        cell_status = "needs_review"
    elif passed:
        cell_status = "passed"
    else:
        cell_status = "in_progress"

    return {
        "status": cell_status,
        "progress": "passed" if passed else "not_passed",
        "score": teacher_score if teacher_score is not None else (scores[-1] if scores else None),
        "score_source": "teacher" if teacher_score is not None else ("ai" if scores else None),
        "attempt_count": len(ordered),
        "evaluated_count": len(scores),
        "latest_ai_score": scores[-1] if scores else None,
        "best_ai_score": max(scores) if scores else None,
        "teacher_score": teacher_score,
        "passed": passed,
        "hints_used": sum(len(_as_list(a.get("prompt_logs"))) for a in ordered),
        "needs_review_count": needs_review_count,
        "review_reasons": reasons,
        "last_activity_at": max((a.get("created_at") or "") for a in ordered) or None,
    }


def summarize_step(step: dict, cells: list[dict]) -> dict:
    """一個步驟在全班的摘要。cells = 每位學生在這一步的格子。"""
    latest = [c["latest_ai_score"] for c in cells if c["latest_ai_score"] is not None]
    effective = [c["score"] for c in cells if c.get("score") is not None]
    return {
        "started": sum(1 for c in cells if c["attempt_count"]),
        "passed": sum(1 for c in cells if c["passed"]),
        "needs_review": sum(1 for c in cells if c["needs_review_count"]),
        # 平均分數：有老師分數的學生用老師分數，其他用 AI 最新分數
        "avg_score": round(sum(effective) / len(effective), 1) if effective else None,
        "avg_latest_ai_score": round(sum(latest) / len(latest), 1) if latest else None,
    }


# ──────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────

@router.get("/overview")
async def get_overview(
    course_id: UUID = Query(..., description="課程 ID"),
    module_id: Optional[UUID] = Query(None, description="只看這個模組；不給就列出課程指派的全部模組"),
    user: CurrentUser = Depends(require_staff),
):
    db = get_supabase()
    course_id, module_id = str(course_id), str(module_id) if module_id else None

    course_res = db.table("courses").select("id, title, is_archived").eq("id", course_id).execute()
    if not course_res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")
    course = course_res.data[0]

    assigned = (
        db.table("course_modules")
        .select("module_id, learning_modules(id, title, is_published)")
        .eq("course_id", course_id)
        .execute()
    ).data or []
    if module_id:
        assigned = [a for a in assigned if a.get("module_id") == module_id]
        if not assigned:
            raise _error(status.HTTP_404_NOT_FOUND, "MODULE_NOT_IN_COURSE", "這個模組沒有指派給這門課程")

    members = (
        db.table("course_members")
        .select("user_id, joined_at, profiles(full_name, email, role)")
        .eq("course_id", course_id)
        .eq("status", "active")
        .execute()
    ).data or []
    # 只列學生：教師或助教自己加入課程試做的紀錄不算進全班統計
    students = [m for m in members if (m.get("profiles") or {}).get("role") not in STAFF_ROLES]
    student_ids = [m["user_id"] for m in students]

    modules_out = []
    for a in assigned:
        mid = a["module_id"]
        module = a.get("learning_modules") or {}

        steps_res = _select_with_fallback(
            lambda cols: db.table("module_steps").select(cols).eq("module_id", mid).order("step_order").execute(),
            STEP_SELECT, STEP_SELECT_BEFORE_010, "module_steps",
        )
        steps = sorted(
            [s for s in steps_res.data or [] if s.get("is_active", True)],
            key=lambda s: s.get("step_order") or 0,
        )
        step_ids = {s["id"] for s in steps}

        attempts = []
        if student_ids and steps:
            attempts = _select_with_fallback(
                lambda cols: (
                    db.table("step_attempts").select(cols)
                    .eq("module_id", mid).in_("user_id", student_ids).execute()
                ),
                ATTEMPT_SELECT, ATTEMPT_SELECT_BEFORE_003, "step_attempts",
            ).data or []

        by_cell: dict[tuple[str, str], list[dict]] = {}
        for att in attempts:
            if att.get("step_id") in step_ids:
                by_cell.setdefault((att["user_id"], att["step_id"]), []).append(att)

        required = [s for s in steps if s.get("is_required", True)]
        rows = []
        cells_by_step: dict[str, list[dict]] = {s["id"]: [] for s in steps}
        for m in students:
            uid = m["user_id"]
            profile = m.get("profiles") or {}
            cells = {}
            for s in steps:
                cell = summarize_cell(by_cell.get((uid, s["id"]), []), s)
                cells[s["id"]] = cell
                cells_by_step[s["id"]].append(cell)
            activity = [c["last_activity_at"] for c in cells.values() if c["last_activity_at"]]
            rows.append({
                "user_id": uid,
                "full_name": profile.get("full_name") or "",
                "email": profile.get("email") or "",
                "joined_at": m.get("joined_at"),
                "required_passed": sum(1 for s in required if cells[s["id"]]["passed"]),
                "required_total": len(required),
                "needs_review_count": sum(c["needs_review_count"] for c in cells.values()),
                "last_activity_at": max(activity) if activity else None,
                "cells": cells,
            })
        # 需要老師看的學生排前面，其次是最近有動靜的
        rows.sort(key=lambda r: r["last_activity_at"] or "", reverse=True)
        rows.sort(key=lambda r: r["needs_review_count"] == 0)

        modules_out.append({
            "module_id": mid,
            "title": module.get("title") or "",
            "is_published": bool(module.get("is_published")),
            "steps": [
                {
                    "step_id": s["id"],
                    "step_order": s.get("step_order"),
                    "step_title": s.get("step_title") or "",
                    "tool_title": (s.get("ai_tools") or {}).get("title") or "",
                    "is_required": bool(s.get("is_required", True)),
                    "pass_score": s.get("pass_score") or DEFAULT_PASS_SCORE,
                    "require_teacher_review": bool(s.get("require_teacher_review")),
                    "summary": summarize_step(s, cells_by_step[s["id"]]),
                }
                for s in steps
            ],
            "students": rows,
        })

    return {
        "data": {
            "course": {"id": course["id"], "title": course.get("title") or "",
                       "is_archived": bool(course.get("is_archived"))},
            "student_count": len(students),
            "modules": modules_out,
        }
    }


@router.get("/students/{user_id}/steps/{step_id}")
async def get_student_step(user_id: UUID, step_id: UUID, user: CurrentUser = Depends(require_staff)):
    """
    點進總覽的一格：這位學生在這一步的每一次作答，新到舊。
    要看完整評分與送出教師判定，用 submission_id 開 /admin/review/{submission_id}。
    """
    db = get_supabase()
    user_id, step_id = str(user_id), str(step_id)

    step_res = (
        db.table("module_steps")
        .select("id, module_id, step_order, step_title, pass_score, require_teacher_review, ai_tools(title)")
        .eq("id", step_id)
        .execute()
    )
    if not step_res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "STEP_NOT_FOUND", "找不到這個步驟")
    step = step_res.data[0]

    profile_res = db.table("profiles").select("id, full_name, email").eq("id", user_id).execute()
    if not profile_res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "STUDENT_NOT_FOUND", "找不到這位學生")
    profile = profile_res.data[0]

    attempts = _select_with_fallback(
        lambda cols: (
            db.table("step_attempts").select(cols)
            .eq("user_id", user_id).eq("step_id", step_id)
            .order("attempt_number", desc=True).execute()
        ),
        DETAIL_SELECT, DETAIL_SELECT.replace(" tutor_action,", ""), "step_attempts",
    ).data or []

    items = []
    for a in attempts:
        evaluation = (_as_list(a.get("ai_evaluations")) or [None])[0]
        review = (_as_list(a.get("teacher_reviews")) or [None])[-1]
        hints = sorted(_as_list(a.get("prompt_logs")), key=lambda h: h.get("created_at") or "")
        items.append({
            "submission_id": a.get("id"),
            "attempt_number": a.get("attempt_number"),
            "submitted_at": a.get("created_at"),
            "status": a.get("status"),
            "tutor_action": a.get("tutor_action"),
            "content": a.get("user_input_content") or "",
            "ai_evaluation": None if evaluation is None else {
                "total_score": evaluation.get("total_score"),
                "confidence": evaluation.get("confidence"),
                "needs_teacher_review": bool(evaluation.get("needs_teacher_review")),
                "feedback_text": evaluation.get("feedback_text") or "",
                "dimension_scores": evaluation.get("dimension_scores") or [],
                "detected_errors": evaluation.get("detected_errors") or [],
            },
            "teacher_review": review,
            "hints": hints,
            "review_reasons": review_reasons(a, step),
        })

    return {
        "data": {
            "student": profile,
            "step": {
                "step_id": step["id"],
                "module_id": step.get("module_id"),
                "step_order": step.get("step_order"),
                "step_title": step.get("step_title") or "",
                "tool_title": (step.get("ai_tools") or {}).get("title") or "",
                "pass_score": step.get("pass_score") or DEFAULT_PASS_SCORE,
            },
            "summary": summarize_cell(attempts, step),
            "attempts": items,
        }
    }


@router.get("/students/{user_id}/profile")
async def get_student_profile(
    user_id: UUID,
    module_id: UUID = Query(..., description="模組 ID"),
    user: CurrentUser = Depends(require_staff),
):
    """
    學生狀態概述（數字版，不呼叫 AI）。

    每次都從作答紀錄即時重算，所以 migration 015 之前的歷史作答也看得到；
    saved_version 是資料庫裡最新保存的側寫版本（AI 助教讀的是那一份），沒有則為 None。
    """
    db = get_supabase()
    user_id, module_id = str(user_id), str(module_id)

    profile_res = db.table("profiles").select("id, full_name, email").eq("id", user_id).execute()
    if not profile_res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "STUDENT_NOT_FOUND", "找不到這位學生")

    module_res = db.table("learning_modules").select("id, title").eq("id", module_id).execute()
    if not module_res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "MODULE_NOT_FOUND", "找不到這個模組")

    attempts = (
        db.table("step_attempts").select(PROFILE_ATTEMPT_SELECT)
        .eq("user_id", user_id).eq("module_id", module_id).execute()
    ).data or []
    latest = load_latest_profile(db, user_id, module_id)

    return {
        "data": {
            "student": profile_res.data[0],
            "module": module_res.data[0],
            "profile": build_profile(attempts),
            "saved_version": (latest or {}).get("version"),
        }
    }
