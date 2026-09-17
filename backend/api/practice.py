"""
練習與 AI 評分 API 路由

負責：
  - 建立練習 Session
  - 接收學生作答並觸發 AI 評分（SSE 串流回傳）與資料庫寫入
  - 提供分層提示
  - 查看練習版本歷程（已串接 step_attempts 與 ai_evaluations）
"""

import json
import logging
import traceback
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.ai.agents.tutor_agent import TutorAgent, StudentContext
from core.ai.agents.coherence_agent import CoherenceAgent, StepAnswer
from core.privacy import detect_pii
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/practice", tags=["practice"])

# 教學 Agent 單例（Phase 2：決策 + 評分 + 提示）
tutor_agent = TutorAgent()

# 整合 Agent 單例（Phase 3：跨步驟一致性檢核）
coherence_agent = CoherenceAgent()


# ──────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    tool_id: str
    case_id: str
    course_id: str


class SubmitAnswerRequest(BaseModel):
    user_id: str
    module_id: str
    step_id: str
    case_id: str | None = None
    content: str
    version_note: str = ""


# ──────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────

import uuid
from datetime import datetime, timezone

@router.post("/sessions", status_code=201)
async def create_session(body: CreateSessionRequest):
    db = get_supabase()
    
    # 1. 取得 tool info
    tool_res = db.table("ai_tools").select("*").eq("id", body.tool_id).execute()
    if not tool_res.data:
        raise HTTPException(status_code=404, detail="Tool not found")
    tool_data = tool_res.data[0]
    
    # 2. 取得 case info
    case_res = db.table("tool_cases").select("*").eq("id", body.case_id).execute()
    if not case_res.data:
        raise HTTPException(status_code=404, detail="Case not found")
    case_data = case_res.data[0]
    
    session_id = str(uuid.uuid4())
    
    return {
        "data": {
            "session_id": session_id,
            "tool": {
                "name": tool_data.get("name", ""),
                "opening_message": tool_data.get("opening_message", "")
            },
            "case": {
                "title": case_data.get("title", ""),
                "content": case_data.get("content", "")
            },
            "task_description": tool_data.get("task_description", ""),
            "started_at": datetime.now(timezone.utc).isoformat()
        }
    }


@router.post("/sessions/{session_id}/submit")
async def submit_answer(
    session_id: str,
    body: SubmitAnswerRequest,
):
    """
    學生提交作答：
      1. 個資偵測（PII Check）
      2. 從 Supabase 讀取真實的 step、ai_tools 與 Rubric 設定
      3. 計算嘗試次數並寫入 step_attempts 記錄
      4. 呼叫 AI 評分並以 SSE 串流回傳
      5. 串流結束後將評分結果存入 ai_evaluations
    """
    try:
        db = get_supabase()

        # 步驟一：個資偵測
        pii_result = detect_pii(body.content)
        if pii_result.has_risk:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PII_DETECTED",
                    "message": pii_result.warning_message,
                    "detected_types": pii_result.detected_types,
                },
            )

        # 步驟二：從 Supabase 讀取真實工具設定與 Rubric
        step_res = db.table("module_steps").select("*, ai_tools(*)").eq("id", body.step_id).single().execute()
        if not step_res.data or "ai_tools" not in step_res.data:
            raise HTTPException(status_code=404, detail="Step or AI Tool not found")

        tool = step_res.data["ai_tools"]
        rubric_criteria = tool.get("rubric_criteria", [])
        system_prompt = tool.get("system_prompt", "你是一位學前特殊教育的評分助理。")
        pass_threshold = tool.get("pass_score", 75)

        # 組裝 Rubric dict（直接傳給 EvaluatorAgent，不再轉成純文字）
        rubric_dict = {
            "pass_threshold_percent": pass_threshold,
            "dimensions": [
                {
                    "name": r.get("dimension"),
                    "weight": r.get("max_score"),
                    "levels": [
                        {"score": lv, "description": r.get("description", "")}
                        for lv in range(1, r.get("max_score", 4) + 1)
                    ] if r.get("levels") is None else r.get("levels")
                }
                for r in rubric_criteria
            ],
        }

        # ── 查詢學生歷程，建立 StudentContext ──────────────────────
        prev_attempts = (
            db.table("step_attempts")
            .select("attempt_number")
            .eq("user_id", body.user_id)
            .eq("step_id", body.step_id)
            .order("attempt_number", desc=True)
            .limit(1)
            .execute()
        )

        next_attempt_number = 1
        if prev_attempts.data:
            next_attempt_number = prev_attempts.data[0]["attempt_number"] + 1

        # 查詢上次分數（從最近一筆 ai_evaluation 取得）
        last_score_percent: float | None = None
        if next_attempt_number > 1:
            prev_eval = (
                db.table("step_attempts")
                .select("id, ai_evaluations(total_score, dimension_scores)")
                .eq("user_id", body.user_id)
                .eq("step_id", body.step_id)
                .order("attempt_number", desc=True)
                .limit(1)
                .execute()
            )
            if prev_eval.data and prev_eval.data[0].get("ai_evaluations"):
                latest_eval = prev_eval.data[0]["ai_evaluations"]
                if isinstance(latest_eval, list) and latest_eval:
                    latest_eval = latest_eval[0]
                dim_scores = latest_eval.get("dimension_scores", [])
                if dim_scores:
                    total = sum(d.get("score", 0) for d in dim_scores)
                    max_total = sum(d.get("max_score", 4) for d in dim_scores)
                    last_score_percent = (total / max_total * 100) if max_total > 0 else None

        # 查詢已使用的提示次數
        hints_res = (
            db.table("prompt_logs")
            .select("id", count="exact")
            .eq("attempt_id",
                prev_attempts.data[0]["id"] if prev_attempts.data else "none")
            .execute()
        )
        hints_used = hints_res.count or 0

        student_context = StudentContext.from_db(
            attempt_number=next_attempt_number,
            last_score_percent=last_score_percent,
            hints_used_count=hints_used,
            pass_threshold=float(pass_threshold),
        )

        # ── 寫入學生作答記錄 ────────────────────────────────────────
        attempt_res = db.table("step_attempts").insert({
            "user_id": body.user_id,
            "module_id": body.module_id,
            "step_id": body.step_id,
            "case_id": body.case_id,
            "attempt_number": next_attempt_number,
            "user_input_content": body.content,
            "status": "submitted"
        }).execute()

        if not attempt_res.data:
            raise HTTPException(status_code=500, detail="Failed to log student attempt")

        attempt_id = attempt_res.data[0]["id"]

        # ── 呼叫 TutorAgent 並以 SSE 串流回傳 ──────────────────────
        async def event_generator() -> AsyncGenerator[str, None]:
            try:
                yield _sse_event("score_start", {
                    "session_id": session_id,
                    "attempt_id": attempt_id,
                    "attempt_number": next_attempt_number,
                })

                final_data = None
                # ✅ Phase 2 升級：TutorAgent 決定要評分、給提示還是鼓勵
                async for chunk in tutor_agent.process_stream(
                    student_answer=body.content,
                    rubric=rubric_dict,
                    system_prompt=system_prompt,
                    student_context=student_context,
                ):
                    yield _sse_event(chunk["type"], chunk["data"])
                    if chunk["type"] == "score_complete":
                        final_data = chunk["data"]
                    elif chunk["type"] == "hint":
                        # 記錄提示使用到 prompt_logs
                        db.table("prompt_logs").insert({
                            "attempt_id": attempt_id,
                            "hint_level": chunk["data"]["level"],
                            "hint_content": chunk["data"]["content"],
                        }).execute()

                # 評分完成時，寫入 ai_evaluations
                if final_data:
                    db.table("ai_evaluations").insert({
                        "attempt_id": attempt_id,
                        "total_score": int(final_data.get("total_score", 0)),
                        "dimension_scores": final_data.get("dimension_scores", []),
                        "evidence_text": body.content[:200],
                        "feedback_text": final_data.get("overall_feedback", ""),
                        "detected_errors": final_data.get("detected_errors", [])
                    }).execute()

                    # 同步更新 step_attempts 狀態為 passed 或 revision_required
                    new_status = "passed" if final_data["passed"] else "revision_required"
                    db.table("step_attempts").update({"status": new_status}).eq("id", attempt_id).execute()

            except Exception as e:
                error_detail = traceback.format_exc()
                logger.error(f"TutorAgent error:\n{error_detail}")
                yield _sse_event("error", {"message": "AI 評分失敗", "detail": str(e)})

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))




@router.get("/sessions/{session_id}/hints")
async def get_hint(session_id: str):
    return {
        "data": {
            "available_level": 1,
            "hint": {"level": 1, "content": "請嘗試從個案的日常作息中找出具體行為表現。"},
            "next_level_available": True,
        }
    }


@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str, user_id: str, step_id: str):
    """
    查看練習版本歷程：
    從資料庫讀取該學生針對特定 step 的歷次嘗試（step_attempts）與對應的 AI 評分（ai_evaluations）
    """
    try:
        db = get_supabase()
        
        # 查詢該學生該步驟的所有嘗試與對應評分
        attempts_res = db.table("step_attempts")\
            .select("*, ai_evaluations(*)")\
            .eq("user_id", user_id)\
            .eq("step_id", step_id)\
            .order("attempt_number", desc=False)\
            .execute()

        return {
            "data": {
                "session_id": session_id,
                "submissions": attempts_res.data if attempts_res.data else []
            }
        }
    except Exception as e:
        logger.error(f"Get history error: {e}")
        raise HTTPException(status_code=500, detail="無法取得歷史紀錄")


@router.post("/modules/{module_id}/coherence-check")
async def check_module_coherence(module_id: str, user_id: str):
    """
    Phase 3：跨步驟整合一致性檢核（SSE 串流）。

    當學生完成模組所有步驟後呼叫，
    CoherenceAgent 會跨步驟檢查 IEP 前後一致性。

    Query params:
        user_id: 學生的 user_id

    SSE 事件序列：
        coherence_start  → 開始，告知共幾條規則
        coherence_check  → 每條規則的結果（逐一）
        coherence_complete → 整體結論與摘要
    """
    db = get_supabase()

    # 讀取此模組此學生所有步驟的最新通過作答
    steps_res = (
        db.table("module_steps")
        .select("id, step_order, step_title")
        .eq("module_id", module_id)
        .order("step_order")
        .execute()
    )
    if not steps_res.data:
        raise HTTPException(status_code=404, detail="Module not found or has no steps")

    step_answers: list[StepAnswer] = []
    for step in steps_res.data:
        # 抓該步驟最新一筆已通過的作答
        attempt_res = (
            db.table("step_attempts")
            .select("user_input_content, status")
            .eq("user_id", user_id)
            .eq("step_id", step["id"])
            .order("attempt_number", desc=True)
            .limit(1)
            .execute()
        )
        if attempt_res.data:
            a = attempt_res.data[0]
            step_answers.append(StepAnswer(
                step_order=step["step_order"],
                step_title=step["step_title"],
                content=a["user_input_content"],
                passed=(a["status"] == "passed"),
            ))

    if len(step_answers) < 2:
        raise HTTPException(
            status_code=400,
            detail="至少需要完成 2 個步驟才能進行一致性檢核"
        )

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for chunk in coherence_agent.check_stream(step_answers):
                yield _sse_event(chunk["type"], chunk["data"])
        except Exception as e:
            error_detail = traceback.format_exc()
            logger.error(f"CoherenceAgent error:\n{error_detail}")
            yield _sse_event("error", {"message": "一致性檢核失敗", "detail": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────
# Helper
# ──────────────────────────────────────────

def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"