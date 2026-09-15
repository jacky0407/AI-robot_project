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

from core.ai.gemini_provider import GeminiProvider
from core.ai.rubric_formatter import format_rubric_to_text
from core.privacy import detect_pii
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/practice", tags=["practice"])

# AI Provider 單例
ai_provider = GeminiProvider()


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

@router.post("/sessions")
async def create_session(body: CreateSessionRequest):
    return {
        "data": {
            "session_id": "temp-session-id",
            "tool": {
                "name": "AI 特教助理",
                "opening_message": "歡迎！請閱讀下方案例並完成作答。",
            },
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
        pass_threshold = 75

        # 將資料庫中的 JSON Rubric 轉換為文字供 AI Provider 使用
        rubric_dict = {
            "pass_threshold_percent": pass_threshold,
            "dimensions": [
                {
                    "name": r.get("dimension"),
                    "weight": r.get("weight", r.get("max_score", 10)),
                    "levels": [{"score": r.get("max_score", 10), "description": r.get("description")}]
                } for r in rubric_criteria
            ]
        }
        rubric_text = format_rubric_to_text(rubric_dict)

        # 計算目前的 attempt_number
        prev_attempts = db.table("step_attempts")\
            .select("attempt_number")\
            .eq("user_id", body.user_id)\
            .eq("step_id", body.step_id)\
            .order("attempt_number", desc=True)\
            .limit(1)\
            .execute()

        next_attempt_number = 1
        if prev_attempts.data:
            next_attempt_number = prev_attempts.data[0]["attempt_number"] + 1

        # 寫入學生作答記錄 (step_attempts)
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

        # 步驟三 & 四：定義 SSE 串流產生器
        async def event_generator() -> AsyncGenerator[str, None]:
            try:
                yield _sse_event("score_start", {"session_id": session_id, "attempt_id": attempt_id})

                final_data = None
                async for chunk in ai_provider.evaluate_stream(
                    student_answer=body.content,
                    rubric_text=rubric_text,
                    system_prompt=system_prompt,
                ):
                    if chunk["type"] == "dimension_score":
                        yield _sse_event("dimension_score", chunk["data"])
                    elif chunk["type"] == "score_complete":
                        final_data = chunk["data"]
                        final_data["passed"] = final_data.get("percentage", 0) >= pass_threshold
                        yield _sse_event("score_complete", final_data)

                # 步驟五：將評分結果寫入 ai_evaluations 資料表
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
                logger.error(f"AI evaluation error:\n{error_detail}")
                yield _sse_event("error", {"message": "AI 評分失敗", "detail": str(e)})

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
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


# ──────────────────────────────────────────
# Helper
# ──────────────────────────────────────────

def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"