"""
多機器人聊天室的兩個 Agent（#68）

  RouterAgent  調度員：學生沒有 @ 指名時，依訊息內容與每個機器人的「出場時機」選一個回答
  ChatBotAgent 被選中的機器人：用它自己的系統指令（ai_tools.system_prompt）回覆，看得到整段對話

參考 ChatGPT 網頁版在對話中用 @ 叫出不同 GPTs：幾個機器人共用同一段對話，
後出場的看得到前面誰說了什麼。每則學生訊息只讓一個機器人回答。

評分不在這裡：評分機器人沿用 EvaluatorAgent／FeedbackAgent（api/chat.py）。
"""

import logging
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.ai.llm_keys import chat_llm
from core.ai.llm_utils import invoke_json, invoke_with_retry

logger = logging.getLogger(__name__)

# 放進 prompt 的對話則數上限：太長會變慢、變貴，最舊的先捨棄
HISTORY_LIMIT = 30
MESSAGE_CHARS = 1500


@dataclass
class ChatBot:
    tool_id: str
    name: str
    when_to_use: str = ""
    system_prompt: str = ""
    role_instruction: str = ""
    # 學習助教（#67）：回答前先查課程教材與學生自己的作答紀錄
    use_materials: bool = False


@dataclass
class ChatTurn:
    role: str          # student | bot
    content: str
    bot_name: str | None = None


def _clip(text: str, limit: int = MESSAGE_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _transcript(history: list[ChatTurn]) -> str:
    lines = []
    for turn in history[-HISTORY_LIMIT:]:
        speaker = "學生" if turn.role == "student" else (turn.bot_name or "機器人")
        lines.append(f"{speaker}：{_clip(turn.content, 400)}")
    return "\n".join(lines)


class RouterAgent:
    """選出這則訊息該由哪個機器人回答；只回傳 bots 裡的一個，選不出來就給第一個"""

    async def route(self, message: str, history: list[ChatTurn], bots: list[ChatBot]) -> tuple[ChatBot, str]:
        if len(bots) == 1:
            return bots[0], "聊天室只有一個對話機器人"

        roster = "\n".join(
            f"{i + 1}. {b.name}：{b.when_to_use or '（沒有寫出場時機）'}" for i, b in enumerate(bots)
        )
        prompt = f"""你是聊天室的調度員，要決定學生這則訊息由哪一個機器人回答（只能選一個）。

【聊天室的機器人與出場時機】
{roster}

【最近的對話】
{_transcript(history) or '（還沒有對話）'}

【學生這則訊息】
{_clip(message)}

依學生這則訊息的內容和對話脈絡，選最適合回答的機器人。
嚴格以 JSON 回傳：{{"bot": 編號（1 到 {len(bots)} 的整數）, "reason": "一句話說明為什麼"}}"""
        try:
            data = await invoke_json(chat_llm(0.0), [HumanMessage(content=prompt)], label="chat_router")
            index = int(data.get("bot")) - 1
            if 0 <= index < len(bots):
                return bots[index], str(data.get("reason") or "").strip()[:200]
            logger.warning(f"調度員回傳超出範圍的編號：{data!r}")
        except Exception as e:  # noqa: BLE001 - 調度失敗不擋對話，改由第一個機器人回答
            logger.warning(f"調度失敗，改由第一個機器人回答：{e}")
        return bots[0], "調度員沒有選出機器人，由第一個機器人回答"


class ChatBotAgent:
    """被選中的機器人回覆一則訊息"""

    async def reply(
        self, bot: ChatBot, others: list[ChatBot], history: list[ChatTurn], message: str, context: str = ""
    ) -> str:
        """context：學習助教查到的教材與作答紀錄（core/materials.context_block），沒有就空字串"""
        roster = "、".join(b.name for b in others if b.tool_id != bot.tool_id) or "（沒有）"
        system = "\n\n".join(part for part in [
            (bot.system_prompt or "").strip(),
            (bot.role_instruction or "").strip(),
            f"你在一個學前特殊教育培訓的聊天室裡，名字是「{bot.name}」。"
            f"聊天室裡的其他機器人：{roster}。學生可以用 @名字 找特定的機器人。\n"
            "只回答屬於你角色的部分；學生的問題比較適合別的機器人時，簡短建議學生 @ 那個機器人。"
            "用繁體中文回答，口吻像老師在對話，不要打分數。",
            context,
        ] if part)

        messages = [SystemMessage(content=system)]
        for turn in history[-HISTORY_LIMIT:]:
            if turn.role == "student":
                messages.append(HumanMessage(content=_clip(turn.content)))
            else:
                # 別的機器人說的話標上名字，才分得出是誰說的
                prefix = "" if turn.bot_name == bot.name else f"（{turn.bot_name or '其他機器人'}說）"
                messages.append(AIMessage(content=prefix + _clip(turn.content)))
        messages.append(HumanMessage(content=_clip(message)))

        text = await invoke_with_retry(chat_llm(0.5), messages, label=f"chat_bot:{bot.name}")
        return text.strip()
