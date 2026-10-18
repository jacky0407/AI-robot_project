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
from core.ai.error_detector import ErrorDetector
from core.ai.hint_engine import HintEngine, parse_teaching_strategy, select_hint
from core.ai.rubric_formatter import normalize_rubric
from core.privacy import detect_pii
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/practice", tags=["practice"])

# 教學 Agent 單例（Phase 2：決策 + 評分 + 提示）
tutor_agent = TutorAgent()

# 整合 Agent 單例（Phase 3：跨步驟一致性檢核）
coherence_agent = CoherenceAgent()

# 錯誤分類偵測器（教授未定義 error_taxonomy 時不會呼叫 LLM）
error_detector = ErrorDetector()

# 分層提示引擎（查詢可用提示層級時使用）
hint_engine = HintEngine()


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
    request_hint: bool = False   # 學生主動按「我需要提示」


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

    # ⚠️ session_id 目前未落庫，submit 也不驗證它。
    #    若要正式支援 Session，需新增 practice_sessions 表。
    return {
        "data": {
            "session_id": session_id,
            "tool": {
                "id": tool_data.get("id"),
                # schema 的欄位是 title / role_instruction，不是 name / opening_message
                "name": tool_data.get("title", ""),
                "opening_message": tool_data.get("role_instruction", ""),
                "target_competency": tool_data.get("target_competency", ""),
            },
            "case": {
                "id": case_data.get("id"),
                "title": case_data.get("title", ""),
                # schema 的欄位是 case_background，不是 content
                "content": case_data.get("case_background", ""),
                "difficulty": case_data.get("difficulty", ""),
            },
            "task_description": tool_data.get("target_competency", ""),
            "started_at": datetime.now(timezone.utc).isoformat(),
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

        # 通過門檻定義在 module_steps.pass_score，不在 ai_tools
        pass_threshold = step_res.data.get("pass_score") or 75

        # 組裝 Rubric dict（直接傳給 EvaluatorAgent，不再轉成純文字）
        # rubric_criteria 的 max_score 是「配分（權重）」，不是量表上限，
        # 展開等級的邏輯統一放在 normalize_rubric()
        rubric_dict = normalize_rubric(
            rubric_criteria,
            pass_threshold,
            tool.get("scale_type"),
        )

        teaching_strategy = tool.get("teaching_strategy")
        error_taxonomy = tool.get("error_taxonomy")

        # ── 查詢學生歷程，建立 StudentContext ──────────────────────
        # 一次查回此步驟所有嘗試的 id、次數與評分（新到舊），避免重複往返
        prev_attempts = (
            db.table("step_attempts")
            .select("id, attempt_number, ai_evaluations(total_score, dimension_scores)")
            .eq("user_id", body.user_id)
            .eq("step_id", body.step_id)
            .order("attempt_number", desc=True)
            .execute()
        )
        attempts = prev_attempts.data or []

        last_attempt = attempts[0] if attempts else None

        next_attempt_number = (last_attempt["attempt_number"] + 1) if last_attempt else 1
        last_score_percent = _extract_last_score_percent(last_attempt)

        # 已用提示數是「整個步驟」累計的：給提示的那次作答不評分，
        # 若只算上一次作答，下一輪又從 0 開始，自動提示會永遠停在第 1 層
        hints_used = _count_hints_used(db, attempts)

        student_context = StudentContext.from_db(
            attempt_number=next_attempt_number,
            last_score_percent=last_score_percent,
            hints_used_count=hints_used,
            pass_threshold=float(pass_threshold),
            last_dimension_scores=_extract_last_dimension_scores(last_attempt),
            requested_hint=body.request_hint,
            # 只拿到提示、沒評分的作答不算進「轉介老師」的次數
            evaluated_attempts=sum(1 for a in attempts if _extract_last_score_percent(a) is not None),
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
                tutor_action = None
                # ✅ Phase 2 升級：TutorAgent 決定要評分、給提示還是鼓勵
                async for chunk in tutor_agent.process_stream(
                    student_answer=body.content,
                    rubric=rubric_dict,
                    system_prompt=system_prompt,
                    student_context=student_context,
                    teaching_strategy=teaching_strategy,
                ):
                    yield _sse_event(chunk["type"], chunk["data"])
                    if chunk["type"] == "tutor_decision":
                        tutor_action = chunk["data"]["action"]
                        _record_tutor_action(db, attempt_id, tutor_action)
                    elif chunk["type"] == "score_complete":
                        final_data = chunk["data"]
                    elif chunk["type"] == "hint":
                        # 記錄提示使用到 prompt_logs，
                        # trigger / source 是學習歷程分析的關鍵維度：
                        # 「自動給的」和「學生主動要的」意義完全不同
                        db.table("prompt_logs").insert({
                            "attempt_id": attempt_id,
                            "hint_level": chunk["data"]["level"],
                            "hint_content": chunk["data"]["content"],
                            "hint_trigger": chunk["data"].get("trigger"),
                            "hint_source": chunk["data"].get("source"),
                        }).execute()

                # 評分完成時，偵測錯誤分類並寫入 ai_evaluations
                if final_data:
                    dimension_scores = final_data.get("dimension_scores", [])

                    # 教授沒定義 error_taxonomy 時，detect() 直接回 []，不呼叫 LLM
                    detected_errors = await error_detector.detect(
                        body.content,
                        error_taxonomy,
                        dimension_scores=dimension_scores,
                    )
                    if detected_errors:
                        yield _sse_event("errors_detected", {"errors": detected_errors})

                    confidence = final_data.get("confidence")

                    db.table("ai_evaluations").insert({
                        "attempt_id": attempt_id,
                        "total_score": round(float(final_data.get("total_score", 0))),
                        "dimension_scores": dimension_scores,
                        # 存 AI 實際引用的原文佐證，不是學生作答的前 200 字
                        "evidence_text": _collect_evidence(dimension_scores),
                        "feedback_text": final_data.get("overall_feedback", ""),
                        "confidence": confidence,
                        "needs_teacher_review": bool(
                            final_data.get("needs_teacher_review", False)
                        ),
                        "detected_errors": detected_errors,
                    }).execute()

                    # 同步更新 step_attempts 狀態為 passed 或 revision_required
                    new_status = "passed" if final_data["passed"] else "revision_required"
                    db.table("step_attempts").update({"status": new_status}).eq("id", attempt_id).execute()
                elif tutor_action in ("give_hint", "escalate"):
                    # 沒有評分（只給提示或建議找老師）→ 學生需要修改後再交，
                    # 不讓狀態永遠停在 submitted
                    db.table("step_attempts").update(
                        {"status": "revision_required"}
                    ).eq("id", attempt_id).execute()

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
async def get_hint(
    session_id: str,
    user_id: str,
    step_id: str,
    reveal: bool = False,
):
    """
    查詢這位學生在此步驟目前可用的分層提示。

    Query params:
        user_id: 學生
        step_id: 步驟
        reveal:  false（預設）只回報有沒有下一層可用，不消耗提示額度；
                 true 才真的取出提示內容並記錄到 prompt_logs

    「有沒有下一層」與「把下一層給我」是兩件事：前端要能先顯示
    「還有 2 層提示可用」而不自動扣掉，學生按下去才算數。
    """
    db = get_supabase()

    step_res = (
        db.table("module_steps")
        .select("id, pass_score, ai_tools(system_prompt, rubric_criteria, scale_type, teaching_strategy)")
        .eq("id", step_id)
        .single()
        .execute()
    )
    if not step_res.data or not step_res.data.get("ai_tools"):
        raise HTTPException(status_code=404, detail="Step or AI Tool not found")

    tool = step_res.data["ai_tools"]
    plan = parse_teaching_strategy(tool.get("teaching_strategy"))

    # 取此步驟所有嘗試（新到舊）：最近一次作答用來掛提示紀錄，
    # 已用提示數整個步驟累計，最弱構面取最近一次「有評分」的作答
    attempt_res = (
        db.table("step_attempts")
        .select("id, attempt_number, user_input_content, ai_evaluations(dimension_scores)")
        .eq("user_id", user_id)
        .eq("step_id", step_id)
        .order("attempt_number", desc=True)
        .execute()
    )
    attempts = attempt_res.data or []
    last_attempt = attempts[0] if attempts else None

    hints_used = _count_hints_used(db, attempts)

    # 上一次若只拿到提示（沒評分），要往前找最近一次有評分的作答
    dimension_scores = next(
        (scores for scores in map(_extract_last_dimension_scores, attempts) if scores),
        [],
    )

    # 先用純邏輯判斷有沒有下一層（不呼叫 LLM）
    if plan.total_levels:
        next_hint_def = select_hint(plan, hints_used, requested_by_student=True)
        has_next = next_hint_def is not None
        total_levels = plan.total_levels
    else:
        # 教授沒設定提示 → 只要有評分結果就能即時生成
        has_next = bool(dimension_scores)
        total_levels = None

    if not reveal:
        return {
            "data": {
                "hints_used": hints_used,
                "total_levels": total_levels,
                "next_level_available": has_next,
                "hint": None,
            }
        }

    if not has_next:
        return {
            "data": {
                "hints_used": hints_used,
                "total_levels": total_levels,
                "next_level_available": False,
                "hint": None,
                "message": "目前沒有更多提示了，請依先前的回饋修改後重新提交。",
            }
        }

    rubric_dict = normalize_rubric(
        tool.get("rubric_criteria", []),
        step_res.data.get("pass_score") or 75,
        tool.get("scale_type"),
    )

    hint = await hint_engine.next_hint(
        teaching_strategy=tool.get("teaching_strategy"),
        student_answer=(last_attempt or {}).get("user_input_content", ""),
        dimension_scores=dimension_scores,
        hints_used=hints_used,
        requested_by_student=True,
        rubric=rubric_dict,
    )

    if hint is None:
        return {
            "data": {
                "hints_used": hints_used,
                "total_levels": total_levels,
                "next_level_available": False,
                "hint": None,
                "message": "目前沒有更多提示了。",
            }
        }

    # 學生主動索取的提示同樣要留下歷程
    if last_attempt:
        db.table("prompt_logs").insert({
            "attempt_id": last_attempt["id"],
            "hint_level": hint.level,
            "hint_content": hint.content,
            "hint_trigger": "student_request",
            "hint_source": hint.source,
        }).execute()

    remaining_check = (
        select_hint(plan, hints_used + 1, requested_by_student=True)
        if plan.total_levels else None
    )

    return {
        "data": {
            "hints_used": hints_used + 1,
            "total_levels": total_levels,
            "next_level_available": bool(remaining_check) if plan.total_levels else True,
            "hint": hint.to_event_data(),
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

    # 讀取此模組此學生每個步驟的最新一次作答（不論是否通過；passed 另外帶給 Agent）
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
        # 抓該步驟最新一筆作答——學生改過的版本才是要比對的內容
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

def _collect_evidence(dimension_scores: list[dict] | None) -> str:
    """
    把各構面的原文佐證整理成一段文字，存進 ai_evaluations.evidence_text。

    舊版存的是學生作答前 200 字，那不是「證據」——
    AI 真正引用的原文在 dimension_scores[].evidence 裡。
    """
    parts = [
        f"[{d.get('dimension', '')}] {d['evidence'].strip()}"
        for d in (dimension_scores or [])
        if (d.get("evidence") or "").strip()
    ]
    return "\n".join(parts)


def _count_hints_used(db, attempts: list[dict]) -> int:
    """
    此步驟所有嘗試累計已給出的提示數。

    沒有前次嘗試就不查——否則會拿空清單去比對 UUID 欄位。
    """
    attempt_ids = [a["id"] for a in attempts if a.get("id")]
    if not attempt_ids:
        return 0
    res = (
        db.table("prompt_logs")
        .select("id", count="exact")
        .in_("attempt_id", attempt_ids)
        .execute()
    )
    return res.count or 0


def _record_tutor_action(db, attempt_id: str, action: str) -> None:
    """
    把 TutorAgent 的決策寫回 step_attempts.tutor_action。

    這是給教師複核佇列用的附加資訊，寫入失敗（例如還沒跑 migration 003）
    只記 log，不中斷學生的評分流程。
    """
    try:
        db.table("step_attempts").update(
            {"tutor_action": action}
        ).eq("id", attempt_id).execute()
    except Exception as e:
        logger.warning(f"寫入 tutor_action 失敗（是否尚未執行 migration 003？）：{e}")


def _extract_last_dimension_scores(last_attempt: dict | None) -> list[dict]:
    """取出上一次評分的各構面結果，給提示引擎找最弱構面用。"""
    if not last_attempt:
        return []

    evaluation = last_attempt.get("ai_evaluations")
    if isinstance(evaluation, list):
        evaluation = evaluation[0] if evaluation else None
    if not evaluation:
        return []

    scores = evaluation.get("dimension_scores")
    return scores if isinstance(scores, list) else []


def _extract_last_score_percent(last_attempt: dict | None) -> float | None:
    """
    從上一次嘗試取出得分百分比，給 TutorAgent 判斷該評分還是給提示。

    優先用 ai_evaluations.total_score（加權後的百分制得分）；
    舊資料若沒有 total_score，才退回用 dimension_scores 換算。
    """
    if not last_attempt:
        return None

    evaluation = last_attempt.get("ai_evaluations")
    if isinstance(evaluation, list):
        evaluation = evaluation[0] if evaluation else None
    if not evaluation:
        return None

    total_score = evaluation.get("total_score")
    if total_score is not None:
        return float(total_score)

    dim_scores = evaluation.get("dimension_scores") or []
    if not dim_scores:
        return None

    total = sum(float(d.get("score", 0)) for d in dim_scores)
    max_total = sum(float(d.get("max_score") or 4) for d in dim_scores)
    return (total / max_total * 100) if max_total > 0 else None


def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"