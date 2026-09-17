"""
教師複核 API 路由

對齊 supabase/schema.sql 的 teacher_reviews 定義：
    attempt_id / teacher_id / decision / final_score / final_feedback / is_published

設計原則（見 AGENTS.md）：
    AI 初評（ai_evaluations）與教師判定（teacher_reviews）分開存放，永不互相覆蓋。
    「是否已複核」以 step_attempts 底下有沒有對應的 teacher_reviews 判斷，
    不在 ai_evaluations 上加旗標。
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review"])

# 對應 schema.sql 的 CHECK 條件
VALID_DECISIONS = ("accept_ai", "modify", "override", "request_retry")


class JudgeRequest(BaseModel):
    # TODO: teacher_id 應改由 Supabase JWT 取得，不該由前端傳入（見 docs/KNOWN_GAPS.md #8）
    teacher_id: str
    decision: str = Field(description="accept_ai | modify | override | request_retry")
    final_score: Optional[int] = None
    final_feedback: str = ""
    is_published: bool = False


# ──────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────

@router.get("/pending")
async def get_pending_reviews(
    tool_id: Optional[str] = Query(None, description="只看某一支機器人的作答"),
    needs_attention: Optional[bool] = Query(
        None, description="True 只回傳 AI 判定未通過（revision_required）的作答"
    ),
):
    """
    列出「已有 AI 初評、但還沒有教師判定」的作答。

    註：schema 中 learning_modules 沒有關聯到 courses，
    因此目前無法依課程篩選，只提供 tool_id 與 needs_attention。
    """
    db = get_supabase()

    res = (
        db.table("step_attempts")
        .select(
            "id, created_at, status, attempt_number, user_id, user_input_content, "
            "profiles(full_name, email), "
            "module_steps(step_title, tool_id, ai_tools(title)), "
            "ai_evaluations(id, total_score, feedback_text), "
            "teacher_reviews(id)"
        )
        .order("created_at", desc=True)
        .execute()
    )

    data = []
    for attempt in res.data or []:
        evaluations = _as_list(attempt.get("ai_evaluations"))
        reviews = _as_list(attempt.get("teacher_reviews"))

        # 待複核 = 有 AI 初評、且尚無教師判定
        if not evaluations or reviews:
            continue

        step = attempt.get("module_steps") or {}
        tool = step.get("ai_tools") or {}
        profile = attempt.get("profiles") or {}
        evaluation = evaluations[0]

        if tool_id and step.get("tool_id") != tool_id:
            continue
        if needs_attention and attempt.get("status") != "revision_required":
            continue

        data.append({
            "submission_id": attempt.get("id"),
            "student": {
                "user_id": attempt.get("user_id"),
                "full_name": profile.get("full_name", ""),
            },
            "step_title": step.get("step_title", ""),
            "tool_name": tool.get("title", ""),
            "attempt_number": attempt.get("attempt_number"),
            "submitted_at": attempt.get("created_at"),
            "status": attempt.get("status"),
            "ai_total_score": evaluation.get("total_score"),
            "needs_attention": attempt.get("status") == "revision_required",
        })

    return {"data": data}


@router.get("/submissions/{attempt_id}")
async def get_submission_detail(attempt_id: str):
    """
    單一作答的完整資訊：學生原文、AI 初評、提示使用紀錄、既有教師判定。
    """
    db = get_supabase()

    attempt_res = (
        db.table("step_attempts")
        .select(
            "*, profiles(full_name, email), "
            "module_steps(step_title, pass_score, ai_tools(title, rubric_criteria)), "
            "ai_evaluations(*), teacher_reviews(*)"
        )
        .eq("id", attempt_id)
        .execute()
    )
    if not attempt_res.data:
        raise HTTPException(status_code=404, detail="Submission not found")

    attempt = attempt_res.data[0]
    evaluations = _as_list(attempt.get("ai_evaluations"))
    reviews = _as_list(attempt.get("teacher_reviews"))
    evaluation = evaluations[0] if evaluations else None

    prompt_res = (
        db.table("prompt_logs")
        .select("*")
        .eq("attempt_id", attempt_id)
        .order("created_at")
        .execute()
    )

    step = attempt.get("module_steps") or {}

    return {
        "data": {
            "submission_id": attempt.get("id"),
            "student": attempt.get("profiles") or {},
            "step": {
                "step_title": step.get("step_title", ""),
                "pass_score": step.get("pass_score"),
                "tool": step.get("ai_tools") or {},
            },
            "attempt_number": attempt.get("attempt_number"),
            "status": attempt.get("status"),
            "content": attempt.get("user_input_content"),
            "ai_evaluation": {
                "total_score": evaluation.get("total_score") if evaluation else None,
                "dimension_scores": evaluation.get("dimension_scores", []) if evaluation else [],
                "feedback_text": evaluation.get("feedback_text", "") if evaluation else "",
                "evidence_text": evaluation.get("evidence_text", "") if evaluation else "",
            },
            "teacher_reviews": reviews,
            "prompt_logs": prompt_res.data or [],
        }
    }


@router.post("/submissions/{attempt_id}/judge", status_code=201)
async def judge_submission(attempt_id: str, body: JudgeRequest):
    """
    寫入教師最終判定（teacher_reviews）。

    不會修改 ai_evaluations——AI 初評永遠保留原始判斷。
    只有 decision = request_retry 時，才把作答狀態改回 revision_required。
    """
    if body.decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_DECISION",
                "message": f"decision 必須是 {', '.join(VALID_DECISIONS)} 其中之一",
            },
        )

    db = get_supabase()

    attempt_res = db.table("step_attempts").select("id").eq("id", attempt_id).execute()
    if not attempt_res.data:
        raise HTTPException(status_code=404, detail="Submission not found")

    review_res = db.table("teacher_reviews").insert({
        "attempt_id": attempt_id,
        "teacher_id": body.teacher_id,
        "decision": body.decision,
        "final_score": body.final_score,
        "final_feedback": body.final_feedback,
        "is_published": body.is_published,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    if not review_res.data:
        raise HTTPException(status_code=500, detail="Failed to save review")

    if body.decision == "request_retry":
        db.table("step_attempts").update(
            {"status": "revision_required"}
        ).eq("id", attempt_id).execute()

    return {"data": review_res.data[0]}


# ──────────────────────────────────────────
# Helper
# ──────────────────────────────────────────

def _as_list(value) -> list:
    """
    PostgREST 的巢狀關聯可能回傳 list、單一 dict 或 None，統一成 list。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
