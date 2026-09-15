"""
練習與 AI 評分 API 路由（已串接 Supabase 資料庫）

負責：
  - 建立練習 Session
  - 接收學生作答並觸發 AI 評分（SSE 串流回傳）並寫入資料庫
  - 提供分層提示
  - 查看練習版本歷程
"""

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.ai.gemini_provider import GeminiProvider
from core.ai.rubric_formatter import format_rubric_to_text
from core.privacy import detect_pii
# 引入我們剛寫好的資料庫操作模組
from supabase.practice_repo import get_tool_and_case_by_ids, save_student_attempt_and_evaluation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/practice", tags=["practice"])

ai_provider = GeminiProvider()


class CreateSessionRequest(BaseModel):
    tool_id: str
    case_id: str
    module_id: str
    step_id: str


class SubmitAnswerRequest(BaseModel):
    content: str
    version_note: str = ""


@router.post("/sessions")
async def create_session(
    body: CreateSessionRequest,
    # current_user: dict = Depends(get_current_user), 
):
    """建立新的練習 Session，從資料庫真實讀取工具與案例"""
    try:
        tool, case = get_tool_and_case_by_ids(body.tool_id, body.case_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail="找不到指定的工具或案例設定")

    return {
        "data": {
            "session_id": f"{body.module_id}-{body.step_id}", # 可對應實體 session id
            "tool": {
                "name": tool.get("title"),
                "opening_message": f"歡迎進入 {tool.get('title')}。請閱讀下方案例並完成作答。",
            },
            "case": {
                "title": case.get("title"),
                "content": case.get("case_background"),
            },
            "task_description": tool.get("target_competency"),
        }
    }


@router.post("/sessions/{session_id}/submit")
async def submit_answer(
    session_id: str,
    body: SubmitAnswerRequest,
    # current_user: dict = Depends(get_current_user),
):
    """學生提交作答，結合個資檢查、AI 串流評分與資料庫儲存"""
    
    # 模擬從 session_id 解析出對應的 tool_id / module_id / step_id / user_id
    # 實務上可將 session 狀態暫存於 Redis 或由前端帶入
    user_id = "temp-user-id" # 待接 get_current_user
    tool_id = "a0000000-0000-0000-0000-000000000001" # 示範用 ID
    module_id = "00000000-0000-0000-0000-000000000001"
    step_id = "00000000-0000-0000-0000-000000000002"
    case_id = "c0000000-0000-0000-0000-000000000001"

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

    # 步驟二：從資料庫讀取真實工具設定 (Rubric & Prompt)
    tool_data, _ = get_tool_and_case_by_ids(tool_id, case_id)
    
    rubric_criteria = tool_data.get("rubric_criteria", [])
    system_prompt = tool_data.get("system_prompt", "你是一位特教評分助理。")
    
    # 組合 Rubric 文字供 AI 閱讀
    rubric_text = format_rubric_to_text(rubric_criteria)
    pass_threshold = 70 # 可由資料庫設定讀取

    final_collected_data = {}

    # 步驟三 & 四：呼叫 AI 並以 SSE 串流回傳
    async def event_generator() -> AsyncGenerator[str, None]:
        nonlocal final_collected_data
        try:
            yield _sse_event("score_start", {"session_id": session_id})

            async for chunk in ai_provider.evaluate_stream(
                student_answer=body.content,
                rubric_text=rubric_text,
                system_prompt=system_prompt,
            ):
                if chunk["type"] == "dimension_score":
                    yield _sse_event("dimension_score", chunk["data"])
                elif chunk["type"] == "score_complete":
                    final_collected_data = chunk["data"]
                    final_collected_data["passed"] = final_collected_data.get("percentage", 0) >= pass_threshold
                    yield _sse_event("score_complete", final_collected_data)

            # 步驟五：將評分結果正式寫入資料庫
            if final_collected_data:
                save_student_attempt_and_evaluation(
                    user_id=user_id,
                    module_id=module_id,
                    step_id=step_id,
                    case_id=case_id,
                    content=body.content,
                    eval_data=final_collected_data
                )

        except Exception as e:
            logger.error(f"AI evaluation error: {e}")
            yield _sse_event("error", {"message": "AI 評分失敗，請稍後再試"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions/{session_id}/hints")
async def get_hint(session_id: str):
    """取得分層提示（可對應查詢 prompt_logs 與 ai_tools 的 teaching_strategy）"""
    return {
        "data": {
            "available_level": 1,
            "hint": {
                "level": 1,
                "content": "請嘗試從『環境互動』與『具體行為』兩個方向重新描述孩子當下的反應。",
            },
            "next_level_available": True,
            "next_level_requires": "student_request",
        }
    }


@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    """取得練習的版本歷程（可從 step_attempts 讀取該學生的歷次嘗試）"""
    # 實務上可在此加入 Supabase 查詢 step_attempts 帶出 AI 評分的語法
    return {"data": {"session_id": session_id, "submissions": []}}


def _sse_event(event_type: str, data: dict) -> str:
    """將資料格式化為標準 SSE 格式"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
