"""
多機器人聊天室 API（#68，migration 020）

學生端：
  GET  /api/chat/modules/{module_id}?step_id=                     一個模組一個聊天室（#108）：階段、整個模組的對話、目前階段的機器人
  GET  /api/chat/steps/{step_id}                                  單一關卡的聊天室資訊與對話（舊畫面用）
  POST /api/chat/sessions/{session_id}/steps/{step_id}/messages   傳一則訊息到某個階段（SSE）
教師端：
  GET  /api/chat/modules/{module_id}/users/{user_id}              某位學生在整個模組的對話（標出每則屬於哪個階段）

一個模組一個聊天室（#108，migration 030）：關卡是聊天室裡的「階段」，訊息、評分、複核、解鎖都照舊記在各階段。
機器人回覆時看得到整個模組最近的對話（跨階段接續）；送出評分只評目前這個階段的對話。

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
from core.auth import CurrentUser, get_current_user, require_staff
from core.chat_room import (
    MODULE_CONTEXT_TURNS,
    chat_partners,
    evaluator_bot,
    find_mention,
    is_chat_step,
    load_bots,
    load_history,
    load_module_history,
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
from core.step_unlock import check_step_unlocked, module_progress
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
        bots = load_bots(db, step_id, module_id=step.get("module_id"))
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


MODULE_STEP_SELECT = "id, step_order, step_title, is_required, is_active, tool_id, ai_tools(title, role_instruction, target_competency)"


def _module_or_404(db, module_id: str, user: CurrentUser) -> dict:
    rows = db.table("learning_modules").select("id, title, is_published").eq("id", module_id).limit(1).execute().data or []
    if not rows or (not user.is_staff and not rows[0].get("is_published")):
        raise _error(404, "MODULE_NOT_FOUND", "找不到這個模組")
    return rows[0]


def _stages(db, user_id: str, module_id: str, *, bypass_locks: bool) -> list[dict]:
    """模組裡每個階段（啟用中的關卡）：進度＋階段開場說明（評分機器人的開場白）"""
    progress = module_progress(db, user_id, module_id, bypass_locks=bypass_locks)
    rows = db.table("module_steps").select(MODULE_STEP_SELECT).eq("module_id", module_id).execute().data or []
    info = {r["id"]: r for r in rows}
    stages = []
    for p in progress:
        tool = (info.get(p["step_id"]) or {}).get("ai_tools") or {}
        stages.append({
            **p,
            "evaluator_name": (tool.get("title") or "").strip(),
            "intro": tool.get("role_instruction") or "",
            "target_competency": tool.get("target_competency") or "",
        })
    return stages


def current_stage(stages: list[dict], wanted: str | None = None) -> dict | None:
    """
    要聊的是哪一個階段：指定的（沒鎖住才行）；否則第一個還沒通過、沒鎖住的；全部通過就停在最後一個。
    """
    if not stages:
        return None
    if wanted:
        for st in stages:
            if st["step_id"] == wanted and not st["locked"]:
                return st
    for st in stages:
        if not st["passed"] and not st["locked"]:
            return st
    return stages[-1]


@router.get("/modules/{module_id}")
async def get_module_chat_room(module_id: str, step_id: str | None = None, user: CurrentUser = Depends(get_current_user)):
    """
    一個模組一個聊天室（#108）：
      stages        每個階段的進度（passed、locked、form_pending）、評分機器人名稱、開場說明
      current       現在聊的是哪個階段（可以用 ?step_id= 回到沒鎖住的前面階段）
      completed     必修階段都通過了
      messages      整個模組的對話（舊 → 新，每則有 step_id，前端依此畫出階段分隔）
      room          目前階段的聊天室資訊（對話機器人含模組共用的、評分機器人、通過方式、今天剩幾次）
    """
    db = get_supabase()
    module = _module_or_404(db, module_id, user)
    stages = _stages(db, user.id, module_id, bypass_locks=user.is_staff)
    current = current_stage(stages, step_id)
    if current is None:
        return {"data": {"module_id": module_id, "module_title": module.get("title"), "stages": [], "current_step_id": None,
                         "completed": False, "messages": [], "room": None}}

    step = _load_chat_step(db, current["step_id"])
    access = check_step_access(db, user, step)
    try:
        evaluator = evaluator_bot(step)
        bots = load_bots(db, current["step_id"], module_id=module_id)
        history = load_module_history(db, user.id, module_id)
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _error(503, "MIGRATION_REQUIRED", "資料庫還沒更新：請執行 supabase/migrations/020_chat_rooms.sql")
        raise
    scoring = has_rubric((step.get("ai_tools") or {}).get("rubric_criteria"))
    required = [st for st in stages if st["is_required"]]
    return {"data": {
        "module_id": module_id,
        "module_title": module.get("title"),
        "stages": stages,
        "current_step_id": current["step_id"],
        "completed": bool(required) and all(st["passed"] for st in required),
        "messages": [public_message(r) for r in history],
        "room": {
            "chat": True,
            "step_id": current["step_id"],
            "step_title": step.get("step_title"),
            # 對話機器人（這個階段的＋模組共用的）；評分機器人另外放在 evaluator
            "bots": [{"tool_id": b.tool_id, "name": b.name, "when_to_use": b.when_to_use, "use_materials": b.use_materials}
                     for b in bots],
            "evaluator": {"tool_id": evaluator.tool_id, "name": evaluator.name} if evaluator else None,
            # 這個階段沒有對話機器人：評分機器人陪學生對話
            "evaluator_chats": not bots and evaluator is not None,
            "pass_mode": effective_pass_mode(step, scoring),
            "scoring": scoring,
            "quota": quota_status(access),
        },
    }}


@router.get("/modules/{module_id}/users/{user_id}")
async def get_student_module_chat(module_id: str, user_id: str, user: CurrentUser = Depends(require_staff)):
    """教師端：某位學生在整個模組的對話，每則標出屬於哪個階段（監控與複核用，不扣次數、不呼叫 AI）"""
    db = get_supabase()
    _module_or_404(db, module_id, user)
    stages = _stages(db, user_id, module_id, bypass_locks=True)
    titles = {st["step_id"]: f"階段 {st['step_order']}：{st['step_title']}" for st in stages}
    try:
        history = load_module_history(db, user_id, module_id)
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _error(503, "MIGRATION_REQUIRED", "資料庫還沒更新：請執行 supabase/migrations/020_chat_rooms.sql")
        raise
    return {"data": {
        "stages": [{k: st[k] for k in ("step_id", "step_order", "step_title", "is_required", "passed", "form_pending")}
                   for st in stages],
        "messages": [{**public_message(r), "stage": titles.get(r.get("step_id"), "")} for r in history],
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
    # 一個模組一個聊天室（#108）：每個關卡都是聊天室的一個階段（還沒跑 030 的作答關卡也可以聊）
    step = _load_chat_step(db, step_id)
    module_id = step.get("module_id")

    # 步驟二：指派 → 解鎖 → Session → 扣次數（任一不過都不寫入、不扣）
    access = check_step_access(db, user, step)
    check_step_unlocked(db, user, step)
    linked_session_id = ensure_session(db, session_id, user.id)
    try:
        evaluator = evaluator_bot(step)
        bots = chat_partners(load_bots(db, step_id, module_id=module_id), evaluator)
        history_rows = load_history(db, user.id, step_id)
        # 機器人回覆時看整個模組最近的對話（跨階段接續）；評分只用這個階段的 history_rows
        context_rows = load_module_history(db, user.id, module_id) if module_id else history_rows
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _error(503, "MIGRATION_REQUIRED", "資料庫還沒更新：請執行 supabase/migrations/020_chat_rooms.sql")
        raise

    candidates = bots + ([evaluator] if evaluator and all(b.tool_id != evaluator.tool_id for b in bots) else [])
    mentioned = find_mention(content, candidates)
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
                    async for event in _reply(db, user, step, bots, mentioned, context_rows, content, linked_session_id):
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
    """
    一則訊息只讓一個機器人回答：有 @ 就是它，否則交給調度員。
    history_rows = 整個模組的對話：最近 MODULE_CONTEXT_TURNS 則給機器人當上下文（跨階段接續），
    學習助教「同一個問題問了幾次」看整個模組。
    """
    turns = to_turns(history_rows[-MODULE_CONTEXT_TURNS:])
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
