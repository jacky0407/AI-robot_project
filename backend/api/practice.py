"""
練習與 AI 評分 API 路由

負責：
  - 建立練習 Session
  - 接收學生作答並觸發 AI 評分（SSE 串流回傳）
  - 提供分層提示
  - 查看練習版本歷程

前置條件：成員二需先實作 auth dependency（get_current_user）
          與資料庫查詢函數（在 database/ 目錄下）。
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/practice", tags=["practice"])

# AI Provider 單例（整個 app 共用一個）
ai_provider = GeminiProvider()


# ──────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    tool_id: str
    case_id: str
    course_id: str


class SubmitAnswerRequest(BaseModel):
    content: str
    version_note: str = ""


# ──────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────

@router.post("/sessions")
async def create_session(
    body: CreateSessionRequest,
    # current_user: dict = Depends(get_current_user),  # 等成員二實作後取消註解
):
    """
    建立新的練習 Session。
    TODO: 等成員二實作 DB 層後，在此儲存 session 到 practice_sessions 表。
    """
    # 暫時回傳假資料，等 DB 層完成後替換
    return {
        "data": {
            "session_id": "temp-session-id",
            "tool": {
                "name": "（待接資料庫）",
                "opening_message": "歡迎！請閱讀下方案例並完成作答。",
            },
            "case": {
                "title": "（待接資料庫）",
                "content": "個案內容將從資料庫讀取...",
            },
            "task_description": "任務說明將從資料庫讀取...",
        }
    }


@router.post("/sessions/{session_id}/submit")
async def submit_answer(
    session_id: str,
    body: SubmitAnswerRequest,
    # current_user: dict = Depends(get_current_user),
):
    """
    學生提交作答。
    流程：
      1. 個資偵測
      2. 從資料庫讀取工具設定（Rubric、System Prompt）
      3. 呼叫 AI 評分引擎
      4. 以 SSE 串流回傳評分結果
      5. 將評分結果存入資料庫（ai_evaluations 表）
    """
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

    # 步驟二：從資料庫讀取工具設定
    # TODO: 等成員二實作 DB 層後，替換以下假資料
    mock_rubric = {
        "pass_threshold_percent": 75,
        "dimensions": [
            {
                "name": "功能性描述",
                "weight": 30,
                "levels": [
                    {"score": 4, "description": "完整描述學生的優勢與需求，有具體行為觀察"},
                    {"score": 3, "description": "描述大致完整，但缺乏部分細節"},
                    {"score": 2, "description": "描述籠統，缺乏行為觀察"},
                    {"score": 1, "description": "未描述或僅列障礙類別"},
                ],
            },
            {
                "name": "去標籤化用語",
                "weight": 20,
                "levels": [
                    {"score": 4, "description": "全程使用優勢本位語言"},
                    {"score": 3, "description": "大部分符合"},
                    {"score": 2, "description": "偶有標籤化描述"},
                    {"score": 1, "description": "大量標籤化用語"},
                ],
            },
        ],
    }
    mock_system_prompt = (
        "你是一位學前特殊教育的評分助理。"
        "請依照提供的評分規準，客觀分析學生作答並給出分數與具體回饋。"
        "回饋語言要正向、具體，引用學生實際作答作為佐證。"
    )

    rubric_text = format_rubric_to_text(mock_rubric)
    pass_threshold = mock_rubric.get("pass_threshold_percent", 75)

    # 步驟三 & 四：呼叫 AI 並以 SSE 串流回傳
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            yield _sse_event("score_start", {"session_id": session_id})

            final_data = None
            async for chunk in ai_provider.evaluate_stream(
                student_answer=body.content,
                rubric_text=rubric_text,
                system_prompt=mock_system_prompt,
            ):
                if chunk["type"] == "dimension_score":
                    yield _sse_event("dimension_score", chunk["data"])
                elif chunk["type"] == "score_complete":
                    final_data = chunk["data"]
                    final_data["passed"] = final_data["percentage"] >= pass_threshold
                    yield _sse_event("score_complete", final_data)

            # 步驟五：將評分結果存入資料庫
            # TODO: 等成員二實作 DB 層後，在此呼叫 db.save_ai_evaluation(...)

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
async def get_hint(
    session_id: str,
    # current_user: dict = Depends(get_current_user),
):
    """
    取得分層提示。
    TODO: 等 DB 層實作後，依學生當前分數與已使用提示層級決定回傳內容。
    """
    return {
        "data": {
            "available_level": 1,
            "hint": {
                "level": 1,
                "content": "（分層提示將從資料庫讀取）",
            },
            "next_level_available": True,
            "next_level_requires": "student_request",
        }
    }


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session_id: str,
    # current_user: dict = Depends(get_current_user),
):
    """
    取得練習的版本歷程。
    TODO: 等 DB 層實作後，從 submissions 表讀取所有版本。
    """
    return {"data": {"session_id": session_id, "submissions": []}}


# ──────────────────────────────────────────
# Helper
# ──────────────────────────────────────────

def _sse_event(event_type: str, data: dict) -> str:
    """將資料格式化為標準 SSE 格式"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
