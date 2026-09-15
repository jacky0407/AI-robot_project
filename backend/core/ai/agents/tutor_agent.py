"""
教學 Agent（Tutor Agent）

根據學生當前狀態，決定最適合的教學行動：
  - EVALUATE：直接評分（第一次作答、或已給過提示後）
  - GIVE_HINT：先給提示（分數不夠、嘗試次數少）
  - ENCOURAGE：鼓勵再修改（分數接近通過門檻）
  - ESCALATE：建議尋求教師幫助（多次低分）

TutorAgent 是整個練習流程的決策入口，
它在內部呼叫 EvaluatorAgent 進行評分。
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator

from core.ai.agents.evaluator_agent import EvaluatorAgent

logger = logging.getLogger(__name__)


class TutorAction(str, Enum):
    EVALUATE  = "evaluate"   # 直接評分
    GIVE_HINT = "give_hint"  # 先給提示，暫不評分
    ENCOURAGE = "encourage"  # 分數接近門檻，鼓勵再試
    ESCALATE  = "escalate"   # 建議尋求教師幫助


@dataclass
class StudentContext:
    """描述學生目前的學習狀態"""
    attempt_number: int          # 第幾次嘗試（從 1 開始）
    last_score_percent: float | None  # 上次分數百分比（第一次作答為 None）
    hints_used_count: int        # 已使用的提示次數
    pass_threshold: float        # 通過門檻（百分比）

    @classmethod
    def from_db(
        cls,
        attempt_number: int,
        last_score_percent: float | None,
        hints_used_count: int,
        pass_threshold: float = 75.0,
    ) -> "StudentContext":
        return cls(
            attempt_number=attempt_number,
            last_score_percent=last_score_percent,
            hints_used_count=hints_used_count,
            pass_threshold=pass_threshold,
        )


class TutorAgent:
    """
    教學決策 Agent。

    決策規則：
    ┌─────────────────────────────────┬──────────────┐
    │ 情況                            │ 行動         │
    ├─────────────────────────────────┼──────────────┤
    │ 第一次作答                       │ EVALUATE     │
    │ 上次分數 >= 門檻                  │ EVALUATE     │
    │ 接近門檻（差 <10%）且嘗試 <=3 次  │ ENCOURAGE    │
    │ 分數 60~門檻，且未用過提示        │ GIVE_HINT    │
    │ 分數 60~門檻，提示已用過          │ EVALUATE     │
    │ 分數 < 60%，嘗試 <= 3 次         │ GIVE_HINT    │
    │ 分數 < 60%，嘗試 > 3 次          │ ESCALATE     │
    └─────────────────────────────────┴──────────────┘
    """

    def __init__(self):
        self._evaluator = EvaluatorAgent()

    def decide_action(self, ctx: StudentContext) -> TutorAction:
        """根據學生狀態決定行動，純邏輯，不呼叫 API。"""

        # 第一次作答 → 直接評分
        if ctx.attempt_number == 1 or ctx.last_score_percent is None:
            return TutorAction.EVALUATE

        score = ctx.last_score_percent
        threshold = ctx.pass_threshold

        # 已通過 → 直接評分（確認是否保持通過）
        if score >= threshold:
            return TutorAction.EVALUATE

        # 接近門檻（差距 < 10%）→ 鼓勵再修改
        if threshold - score < 10 and ctx.attempt_number <= 3:
            return TutorAction.ENCOURAGE

        # 分數 60% 以上但未達門檻
        if score >= 60:
            if ctx.hints_used_count == 0:
                return TutorAction.GIVE_HINT
            return TutorAction.EVALUATE  # 給過提示了，直接評分

        # 分數低於 60%
        if ctx.attempt_number <= 3:
            return TutorAction.GIVE_HINT
        return TutorAction.ESCALATE

    async def process_stream(
        self,
        student_answer: str,
        rubric: dict,
        system_prompt: str,
        student_context: StudentContext,
        hint_generator=None,  # 選填：提示生成函式 async (answer, context) -> str
    ) -> AsyncGenerator[dict, None]:
        """
        教學主流程（SSE 串流版本）。

        根據決策結果：
        - EVALUATE / ENCOURAGE → 呼叫 EvaluatorAgent 評分
        - GIVE_HINT → 生成提示，不評分
        - ESCALATE → 回傳建議尋求教師幫助的訊息
        """
        action = self.decide_action(student_context)
        logger.info(
            f"TutorAgent decision: {action} "
            f"(attempt={student_context.attempt_number}, "
            f"last_score={student_context.last_score_percent})"
        )

        # 先告知前端目前的行動決策
        yield {
            "type": "tutor_decision",
            "data": {
                "action": action.value,
                "attempt_number": student_context.attempt_number,
                "last_score_percent": student_context.last_score_percent,
            },
        }

        if action == TutorAction.EVALUATE:
            async for chunk in self._evaluator.evaluate_stream(
                student_answer=student_answer,
                rubric=rubric,
                system_prompt=system_prompt,
            ):
                yield chunk

        elif action == TutorAction.ENCOURAGE:
            # 鼓勵訊息 + 仍然評分（讓學生知道進步了多少）
            yield {
                "type": "encouragement",
                "data": {
                    "message": (
                        f"你已經非常接近通過標準了！"
                        f"（上次：{student_context.last_score_percent:.0f}%，"
                        f"目標：{student_context.pass_threshold:.0f}%）"
                        "再仔細修改一下，你可以的！"
                    ),
                },
            }
            async for chunk in self._evaluator.evaluate_stream(
                student_answer=student_answer,
                rubric=rubric,
                system_prompt=system_prompt,
            ):
                yield chunk

        elif action == TutorAction.GIVE_HINT:
            hint_level = student_context.hints_used_count + 1
            if hint_generator:
                hint_content = await hint_generator(
                    student_answer, student_context
                )
            else:
                hint_content = _default_hint(
                    hint_level, student_context.last_score_percent
                )

            yield {
                "type": "hint",
                "data": {
                    "level": hint_level,
                    "content": hint_content,
                    "message": "先依照提示修改後，再重新提交作答。",
                },
            }

        elif action == TutorAction.ESCALATE:
            yield {
                "type": "escalation",
                "data": {
                    "message": (
                        f"你已嘗試 {student_context.attempt_number} 次，"
                        "建議與老師討論後再繼續練習。"
                        "老師可以幫助你找到思考方向！"
                    ),
                    "suggest_teacher_review": True,
                },
            }


def _default_hint(hint_level: int, last_score: float | None) -> str:
    """當沒有提供自訂提示生成器時，依層級給出預設提示。"""
    hints = {
        1: "請重新閱讀案例，思考：你描述的是學生的行為表現，還是只列出診斷類別？",
        2: "一個好的功能性描述應該包含：在什麼情境下、展現什麼行為、頻率或程度如何。",
        3: "參考結構：「[學生名] 在 [具體情境] 時，能/無法 [具體行為]，[頻率/程度說明]。」",
        4: "範例：「小明在自由遊戲時段，能主動走向同伴並發起互動，每次持續約 5 分鐘。」",
    }
    return hints.get(hint_level, hints[4])
