"""
只給回饋、不評分的 Agent（#65：機器人沒有評分規準時用）

老師建立機器人時可以不填評分規準；這種機器人收到作答後只依它的系統指令給一段文字回饋，
不產生分數，也不寫 ai_evaluations（不寫假分數）。是否通過由關卡的 pass_mode 決定
（core/pass_mode.py）。

一次作答只呼叫一次 LLM；記憶（core/ai/memory.py）一樣放進 prompt，讓回饋接得上前幾次。
"""

from langchain_core.messages import HumanMessage, SystemMessage

from core.ai.llm_keys import chat_llm
from core.ai.llm_utils import invoke_with_retry

DEFAULT_SYSTEM_PROMPT = "你是一位學前特殊教育的學習夥伴，請針對學生的作答給予具體、溫和的回饋。"

FEEDBACK_RULES = """
請針對學生這次的作答給一段回饋（繁體中文，300 字以內）：
- 先具體指出寫得好的地方，再指出一到兩個最值得修改的地方，並說明怎麼改
- 不要打分數、不要說「通過／不通過」
- 不要重寫整份答案給學生
"""


def _memory_block(memory_text: str) -> str:
    if not memory_text:
        return ""
    return (
        f"\n{memory_text}\n\n"
        "（以上是學習記憶：可以指出比上次進步的地方、提醒仍重複的問題。"
        "若有教師複核意見，回饋不得與教師意見矛盾。）\n"
    )


class FeedbackAgent:
    async def feedback(self, student_answer: str, system_prompt: str | None, memory_text: str = "") -> str:
        messages = [
            SystemMessage(content=(system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT),
            HumanMessage(content=(
                f"{FEEDBACK_RULES}{_memory_block(memory_text)}\n"
                f"【學生這次的作答】\n{student_answer}"
            )),
        ]
        text = await invoke_with_retry(chat_llm(0.4, light=True), messages, label="feedback_only")
        return text.strip()
