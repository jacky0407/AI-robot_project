"""
多機器人聊天室 API（#68，migration 020）

學生端：
  GET  /api/chat/steps/{step_id}                                  聊天室資訊與自己的對話
  POST /api/chat/sessions/{session_id}/steps/{step_id}/messages   傳一則訊息（SSE）

每則學生訊息：
  1. 個資偵測（命中 400，不存、不扣次數、不呼叫 LLM）
  2. 指派、解鎖、Session 檢查，扣一次每日額度（每日次數按「每一則訊息」計算）
  3. 有 @ 對話機器人 → 它回答；@ 評分機器人或 evaluate=true → 評整段對話；
     都沒有 → 調度員依內容選一個對話機器人（每則只由一個機器人回答）
  4. 回覆與評分以 SSE 串流

評分沿用原本的資料流：整段對話寫成一筆 step_attempts（user_input_content = 對話紀錄），
AI 初評寫 ai_evaluations、教師判定寫 teacher_reviews，兩者分開存放、互不覆蓋；
通過方式沿用 module_steps.pass_mode（core/pass_mode.py）。
"""

import logging
import traceback
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api import practice
from core.access import check_step_access, consume_quota, quota_status, refund_quota
from core.ai.agents.chat_agents import ChatBotAgent, RouterAgent
from core.ai.llm_keys import use_api_key
from core.ai_usage import check_budget, course_for_access, track_usage, usage_tool
from core.ai.rubric_formatter import normalize_rubric
from core.tool_lock import apply_locked_version
from core.auth import CurrentUser, get_current_user
from core.chat_room import (
    evaluator_bot,
    find_mention,
    is_chat_step,
    load_bots,
    load_history,
    migration_missing,
    public_message,
    save_message,
    to_turns,
    transcript_for_evaluation,
)
from core.learner_profile import refresh_learner_profile
from core.config import get_settings
from core.materials import (
    answer_policy,
    context_block,
    count_same_question,
    courses_for_module,
    own_attempt_lines,
    prior_questions_to,
    search_materials,
)
from core.pass_mode import effective_pass_mode, has_rubric, status_after_submit
from core.step_forward import extract_and_save
from core.practice_session import ensure_session, parse_session_id
from core.privacy import detect_pii
from core.step_unlock import check_step_unlocked
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

router_agent = RouterAgent()
chat_bot_agent = ChatBotAgent()

MAX_MESSAGE_CHARS = 2000


class ChatMessageRequest(BaseModel):
    content: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    # 按「送出評分」：不用打字，直接請評分機器人評整段對話
    evaluate: bool = False


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _load_chat_step(db, step_id: str) -> dict:
    res = db.table("module_steps").select("*, ai_tools(*)").eq("id", step_id).limit(1).execute()
    if not res.data:
        raise _error(404, "STEP_NOT_FOUND", "找不到這個關卡")
    # 模組停在評分機器人的舊版時，評分與對話用那一版的內容（#89，migration 024）
    return apply_locked_version(db, res.data[0])


@router.get("/steps/{step_id}")
async def get_chat_room(step_id: str, user: CurrentUser = Depends(get_current_user)):
    """
    聊天室資訊：對話機器人、評分機器人、自己的對話、今天剩幾次。
    不是聊天室的關卡回 {"chat": false}，前端改顯示原本的作答區。
    """
    db = get_supabase()
    step = _load_chat_step(db, step_id)
    if not is_chat_step(step):
        return {"data": {"chat": False}}

    access = check_step_access(db, user, step)
    try:
        bots = load_bots(db, step_id)
        history = load_history(db, user.id, step_id)
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _error(503, "MIGRATION_REQUIRED", "資料庫還沒更新：請執行 supabase/migrations/020_chat_rooms.sql")
        raise

    evaluator = evaluator_bot(step)
    scoring = has_rubric((step.get("ai_tools") or {}).get("rubric_criteria"))
    return {"data": {
        "chat": True,
        "step_id": step_id,
        "step_title": step.get("step_title"),
        "bots": [{"tool_id": b.tool_id, "name": b.name, "when_to_use": b.when_to_use, "use_materials": b.use_materials}
                 for b in bots],
        "evaluator": {"tool_id": evaluator.tool_id, "name": evaluator.name} if evaluator else None,
        "pass_mode": effective_pass_mode(step, scoring),
        "scoring": scoring,
        "messages": [public_message(r) for r in history],
        "quota": quota_status(access),
    }}


@router.post("/sessions/{session_id}/steps/{step_id}/messages")
async def send_message(
    session_id: str,
    step_id: str,
    body: ChatMessageRequest,
    user: CurrentUser = Depends(get_current_user),
):
    session_id = parse_session_id(session_id)
    content = (body.content or "").strip()
    if not content and not body.evaluate:
        raise _error(400, "EMPTY_MESSAGE", "請先輸入訊息")

    # 步驟一：個資偵測（命中就不存、不扣次數）
    pii = detect_pii(content)
    if content and pii.has_risk:
        raise HTTPException(status_code=400, detail={
            "code": "PII_DETECTED",
            "message": pii.warning_message,
            "detected_types": pii.detected_types,
        })

    db = get_supabase()
    step = _load_chat_step(db, step_id)
    if not is_chat_step(step):
        raise _error(400, "NOT_CHAT_STEP", "這個關卡不是聊天室")
    module_id = step.get("module_id")

    # 步驟二：指派 → 解鎖 → Session → 扣次數（任一不過都不寫入、不扣）
    access = check_step_access(db, user, step)
    check_step_unlocked(db, user, step)
    linked_session_id = ensure_session(db, session_id, user.id)
    try:
        bots = load_bots(db, step_id)
        history_rows = load_history(db, user.id, step_id)
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _error(503, "MIGRATION_REQUIRED", "資料庫還沒更新：請執行 supabase/migrations/020_chat_rooms.sql")
        raise

    evaluator = evaluator_bot(step)
    mentioned = find_mention(content, bots + ([evaluator] if evaluator else []))
    wants_evaluation = body.evaluate or (evaluator is not None and mentioned is not None and mentioned.tool_id == evaluator.tool_id)
    if not wants_evaluation and not bots:
        raise _error(400, "NO_CHAT_BOTS", "這個聊天室還沒有可以對話的機器人，請老師到模組管理設定")

    # 本月 AI 預算用完 → 429，不存訊息、不扣次數（#81）。送出評分不擋：讓已經聊完的學生能完成這一關
    course_id = course_for_access(db, user.id, module_id, access)
    budget = None if wants_evaluation else check_budget(db, course_id=course_id, access_mode=access.mode)
    quota = consume_quota(db, access)
    student_key = practice._student_api_key(db, user, access)
    usage = practice._usage_context(db, user, access, course_id, step, student_key)

    student_row = None
    if content:
        student_row = save_message(db, {
            "user_id": user.id, "module_id": module_id, "step_id": step_id,
            "session_id": linked_session_id, "role": "student", "content": content,
        })

    async def events() -> AsyncGenerator[str, None]:
        with use_api_key(student_key), track_usage(usage):
            try:
                yield practice._sse_event("message_saved", {
                    "message": public_message(student_row) if student_row else None,
                    "quota": quota,
                    "budget": budget,
                })
                if wants_evaluation:
                    async for event in _evaluate(db, user, step, evaluator, history_rows + ([student_row] if student_row else []),
                                                 linked_session_id):
                        yield event
                else:
                    async for event in _reply(db, user, step, bots, mentioned, history_rows, content, linked_session_id):
                        yield event
            except Exception as e:  # noqa: BLE001
                logger.error(f"chat error:\n{traceback.format_exc()}")
                yield practice._sse_event("error", {"message": "機器人回覆失敗，請稍後再試", "detail": str(e)})
                refunded = refund_quota(db, access) if quota else None
                if refunded:
                    yield practice._sse_event("quota_refunded", {"reason": "chat_error", "quota": refunded})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _reply(db, user, step, bots, mentioned, history_rows, content, session_id) -> AsyncGenerator[str, None]:
    """一則訊息只讓一個機器人回答：有 @ 就是它，否則交給調度員"""
    turns = to_turns(history_rows)
    if mentioned is not None:
        bot, routing = mentioned, {"by": "mention", "reason": f"學生 @{mentioned.name}"}
    elif len(bots) == 1:
        bot, routing = bots[0], {"by": "only_bot", "reason": "聊天室只有一個對話機器人"}
    else:
        bot, reason = await router_agent.route(content, turns, bots)
        routing = {"by": "router", "reason": reason}

    yield practice._sse_event("bot_selected", {"tool_id": bot.tool_id, "bot_name": bot.name, **routing})
    context = ""
    if bot.use_materials:
        # 學習助教（#67）：先查這門課的教材與學生自己的作答紀錄，再回答
        context, sources = await _tutor_context(db, user, step, content)
        routing["sources"] = sources
        yield practice._sse_event("materials_used", {"sources": sources})
        # 不直接給答案；同一個問題問到第 N 次才給（老師 11/24 確認）
        repeats = await count_same_question(content, prior_questions_to(history_rows, bot.tool_id))
        mode, rule = answer_policy(repeats + 1, get_settings().tutor_answer_after)
        routing["answer_mode"] = mode
        routing["times_asked"] = repeats + 1
        context = f"{rule}\n\n{context}"
    with usage_tool(bot.tool_id):  # 回覆的費用記在回答的機器人名下
        text = await chat_bot_agent.reply(bot, bots, turns, content, context)
    row = save_message(db, {
        "user_id": user.id, "module_id": step.get("module_id"), "step_id": step["id"],
        "session_id": session_id, "role": "bot", "tool_id": bot.tool_id, "bot_name": bot.name,
        "content": text, "routing": routing,
    })
    yield practice._sse_event("bot_reply", {"message": public_message(row)})


async def _tutor_context(db, user, step, question: str) -> tuple[str, list[str]]:
    """
    學習助教的參考資料：這門課的教材（向量搜尋，不跨課程）＋學生自己的作答紀錄。
    任一部分失敗都只記 log：沒有參考資料時助教會說「教材裡沒有找到」，不擋對話。
    """
    module_id = step.get("module_id")
    chunks, lines = [], []
    try:
        course_ids = courses_for_module(db, user.id, module_id, is_staff=user.is_staff)
        chunks = await search_materials(db, course_ids, question)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"教材搜尋失敗（是否尚未執行 migration 021？）：{e}")
    try:
        lines = own_attempt_lines(db, user.id, module_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取作答紀錄失敗：{e}")
    return context_block(chunks, lines), [c.label for c in chunks]


async def _evaluate(db, user, step, evaluator, rows, session_id) -> AsyncGenerator[str, None]:
    """
    評分機器人評整段對話。結果照舊寫 step_attempts／ai_evaluations（AI 初評），
    老師之後在複核頁寫 teacher_reviews（教師判定），兩者分開存放。
    """
    if evaluator is None:
        raise RuntimeError("這個聊天室沒有評分機器人")
    transcript = transcript_for_evaluation(rows)
    if not transcript.strip():
        yield practice._sse_event("error", {"message": "還沒有對話內容可以評分，先和機器人聊聊再送出評分"})
        return

    tool = step.get("ai_tools") or {}
    scoring = has_rubric(tool.get("rubric_criteria"))
    pass_mode = effective_pass_mode(step, scoring)
    pass_score = step.get("pass_score") or 75
    system_prompt = tool.get("system_prompt") or "你是一位學前特殊教育的評分助理。"
    yield practice._sse_event("bot_selected", {
        "tool_id": evaluator.tool_id, "bot_name": evaluator.name, "by": "evaluate", "reason": "送出評分",
    })

    last = (
        db.table("step_attempts").select("attempt_number").eq("user_id", user.id).eq("step_id", step["id"])
        .order("attempt_number", desc=True).limit(1).execute()
    )
    attempt_number = ((last.data or [{}])[0].get("attempt_number") or 0) + 1
    attempt_row = {
        "user_id": user.id, "module_id": step.get("module_id"), "step_id": step["id"],
        "attempt_number": attempt_number, "user_input_content": transcript, "status": "submitted",
        "structured_data": {"source": "chat", "message_count": len(rows)},
    }
    if session_id:
        attempt_row["session_id"] = session_id
    attempt_res = db.table("step_attempts").insert(attempt_row).execute()
    attempt_id = attempt_res.data[0]["id"]
    yield practice._sse_event("score_start", {
        "attempt_id": attempt_id, "attempt_number": attempt_number, "pass_mode": pass_mode, "scoring": scoring,
    })

    if scoring:
        rubric = normalize_rubric(tool.get("rubric_criteria"), pass_score, tool.get("scale_type"))
        final = None
        async for chunk in practice.tutor_agent._evaluator.evaluate_stream(transcript, rubric, system_prompt):
            yield practice._sse_event(chunk["type"], chunk["data"])
            if chunk["type"] == "score_complete":
                final = chunk["data"]
        if final is None:
            raise RuntimeError("評分沒有完成")
        practice._insert_evaluation(db, {
            "attempt_id": attempt_id,
            "total_score": round(float(final.get("total_score", 0))),
            "dimension_scores": final.get("dimension_scores", []),
            "evidence_text": practice._collect_evidence(final.get("dimension_scores")),
            "feedback_text": final.get("overall_feedback", ""),
            "confidence": final.get("confidence"),
            "needs_teacher_review": bool(final.get("needs_teacher_review", False)),
            "detected_errors": [],
            # 評分當時的評分機器人版本（migration 022；還沒跑時 _insert_evaluation 會拿掉）
            "tool_version": tool.get("version"),
        })
        ai_passed = bool(final.get("passed"))
        summary = f"評分：{round(float(final.get('percentage', final.get('total_score', 0))))} 分\n{final.get('overall_feedback', '')}".strip()
    else:
        feedback = await practice.feedback_agent.feedback(transcript, system_prompt)
        yield practice._sse_event("feedback_complete", {"feedback": feedback})
        db.table("step_attempts").update(
            {"structured_data": {**attempt_row["structured_data"], "ai_feedback": feedback}}
        ).eq("id", attempt_id).execute()
        ai_passed, summary = None, feedback

    new_status = status_after_submit(pass_mode, ai_passed)
    if new_status:
        db.table("step_attempts").update({"status": new_status}).eq("id", attempt_id).execute()
    yield practice._sse_event("pass_status", practice._pass_status(pass_mode, new_status))
    if new_status == "passed":
        # 通過時抽出要傳給後面關卡的欄位（#84）；聊天室從整段對話抽
        structured = {**attempt_row["structured_data"], **({"ai_feedback": summary} if ai_passed is None else {})}
        values = await extract_and_save(db, attempt_id, step, transcript, structured)
        if values:
            yield practice._sse_event("forward_extracted", {"fields": values})

    row = save_message(db, {
        "user_id": user.id, "module_id": step.get("module_id"), "step_id": step["id"],
        "session_id": session_id, "role": "bot", "tool_id": evaluator.tool_id, "bot_name": evaluator.name,
        "content": summary, "routing": {"by": "evaluate", "reason": "送出評分"}, "attempt_id": attempt_id,
    })
    yield practice._sse_event("bot_reply", {"message": public_message(row)})
    refresh_learner_profile(db, user.id, step.get("module_id"), attempt_id)
