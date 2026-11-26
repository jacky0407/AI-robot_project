"""
多機器人聊天室的資料存取（#68，migration 020）

聊天室關卡（module_steps.step_type = 'chat'）：
  - 評分機器人 = 關卡本身的 module_steps.tool_id（ai_tools 帶評分規準與通過方式）
  - 對話機器人 = chat_room_bots（名稱、出場時機）
  - 對話 = chat_messages，一位學生在一個關卡一串對話

評分機器人在聊天室裡也能被 @，被 @ 時代表學生要送出評分（api/chat.py）。
"""

import re
from typing import Optional

from core.ai.agents.chat_agents import ChatBot, ChatTurn

BOT_SELECT_020 = "id, tool_id, display_name, when_to_use, sort_order, ai_tools(id, title, system_prompt, role_instruction, status)"
# migration 021 之後：用 * 一起讀 use_materials（學習助教）與 tool_version（024，停在舊版）
BOT_SELECT = "*, ai_tools(id, title, system_prompt, role_instruction, status, version)"
MESSAGE_SELECT = "id, role, tool_id, bot_name, content, routing, attempt_id, created_at"

# 一次讀進來的對話則數（畫面與 prompt 共用）；評分時讀整串
HISTORY_LOAD_LIMIT = 200
# 送去評分的對話字數上限（與作答字數上限同級，太長就從最舊的捨棄）
TRANSCRIPT_CHARS = 12000

UNDEFINED_TABLE = "42P01"
UNDEFINED_COLUMN = "42703"
TABLE_NOT_FOUND = "PGRST205"


def is_chat_step(step: dict) -> bool:
    return isinstance(step, dict) and step.get("step_type") == "chat"


def migration_missing(error: Exception) -> bool:
    """還沒跑 020（沒有 chat_* 資料表或 step_type 欄位）"""
    code = getattr(error, "code", None)
    return code in (UNDEFINED_TABLE, UNDEFINED_COLUMN, TABLE_NOT_FOUND)


def evaluator_bot(step: dict) -> Optional[ChatBot]:
    tool = step.get("ai_tools") or {}
    if not tool:
        return None
    return ChatBot(
        tool_id=str(step.get("tool_id") or tool.get("id") or ""),
        name=(tool.get("title") or "評分機器人").strip(),
        when_to_use="學生要送出評分時",
        system_prompt=tool.get("system_prompt") or "",
        role_instruction=tool.get("role_instruction") or "",
    )


def _select_bots(db, step_id: str):
    """還沒跑 021（沒有 use_materials）時退回舊欄位：聊天室照常，只是沒有學習助教"""
    try:
        return db.table("chat_room_bots").select(BOT_SELECT).eq("step_id", step_id).order("sort_order").execute()
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) != UNDEFINED_COLUMN or "use_materials" not in str(e):
            raise
        return db.table("chat_room_bots").select(BOT_SELECT_020).eq("step_id", step_id).order("sort_order").execute()


def load_bots(db, step_id: str, *, published_only: bool = True) -> list[ChatBot]:
    """聊天室的對話機器人（不含評分機器人），依 sort_order；已封存或未發布的機器人學生端不出現"""
    from core.tool_lock import apply_locked_version

    res = _select_bots(db, step_id)
    bots = []
    cache: dict = {}
    for row in sorted(res.data or [], key=lambda r: r.get("sort_order") or 0):
        # 模組停在這支機器人的舊版時，用那一版的角色與指令（#89，migration 024）
        apply_locked_version(db, row, cache=cache)
        tool = row.get("ai_tools") or {}
        if published_only and tool.get("status") != "published":
            continue
        bots.append(ChatBot(
            tool_id=str(row.get("tool_id")),
            name=(row.get("display_name") or tool.get("title") or "機器人").strip(),
            when_to_use=row.get("when_to_use") or "",
            system_prompt=tool.get("system_prompt") or "",
            role_instruction=tool.get("role_instruction") or "",
            use_materials=bool(row.get("use_materials")),
        ))
    return bots


def load_bot_rows(db, step_id: str) -> list[dict]:
    """教師端設定頁用：原始的 chat_room_bots（含機器人名稱與狀態）"""
    res = _select_bots(db, step_id)
    rows = sorted(res.data or [], key=lambda r: r.get("sort_order") or 0)
    return [{
        "id": r.get("id"),
        "tool_id": r.get("tool_id"),
        "display_name": r.get("display_name"),
        "when_to_use": r.get("when_to_use") or "",
        "sort_order": r.get("sort_order") or 0,
        "use_materials": bool(r.get("use_materials")),
        "tool_version": r.get("tool_version"),  # 停在第幾版（migration 024）；None = 跟著最新版
        "tool_title": (r.get("ai_tools") or {}).get("title"),
        "tool_status": (r.get("ai_tools") or {}).get("status"),
    } for r in rows]


def find_mention(text: str, bots: list[ChatBot]) -> Optional[ChatBot]:
    """
    訊息裡第一個 @名字 對到的機器人；名字可以含空白，取最長的那個（「@IEP 小幫手」優先於「@IEP」）。
    對不到任何機器人回 None（交給調度員）。
    """
    for match in re.finditer(r"[@＠]", text or ""):
        rest = text[match.end():].lstrip()
        candidates = [b for b in bots if b.name and rest.lower().startswith(b.name.lower())]
        if candidates:
            return max(candidates, key=lambda b: len(b.name))
    return None


def load_history(db, user_id: str, step_id: str, limit: int = HISTORY_LOAD_LIMIT) -> list[dict]:
    """這位學生在這個關卡的對話，舊 → 新"""
    res = (
        db.table("chat_messages")
        .select(MESSAGE_SELECT)
        .eq("user_id", user_id)
        .eq("step_id", step_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = res.data or []
    return sorted(rows, key=lambda r: r.get("created_at") or "")


def to_turns(rows: list[dict]) -> list[ChatTurn]:
    return [ChatTurn(role=r.get("role") or "student", content=r.get("content") or "", bot_name=r.get("bot_name")) for r in rows]


def save_message(db, row: dict) -> dict:
    res = db.table("chat_messages").insert(row).execute()
    return (res.data or [row])[0]


def transcript_for_evaluation(rows: list[dict], limit: int = TRANSCRIPT_CHARS) -> str:
    """
    整段對話整理成評分機器人要評的文字（存進 step_attempts.user_input_content）。
    太長時從最舊的捨棄，保留最近的對話；評分結果本身的訊息不放進去。
    """
    lines = []
    for r in rows:
        if (r.get("routing") or {}).get("by") == "evaluate" and r.get("role") == "bot":
            continue
        speaker = "學生" if r.get("role") == "student" else (r.get("bot_name") or "機器人")
        lines.append(f"{speaker}：{(r.get('content') or '').strip()}")

    kept, total = [], 0
    for line in reversed(lines):
        total += len(line) + 1
        if total > limit and kept:
            break
        kept.append(line)
    return "\n".join(reversed(kept))


def public_message(row: dict) -> dict:
    """回給前端的訊息（不含內部欄位）"""
    routing = row.get("routing") or {}
    return {
        "id": row.get("id"),
        "role": row.get("role"),
        "bot_name": row.get("bot_name"),
        "tool_id": row.get("tool_id"),
        "content": row.get("content"),
        "routed_by": routing.get("by"),
        # 學習助教引用的教材段落（例如「IEP 撰寫指引・第 2 段」）
        "sources": routing.get("sources") or [],
        "attempt_id": row.get("attempt_id"),
        "created_at": row.get("created_at"),
    }
