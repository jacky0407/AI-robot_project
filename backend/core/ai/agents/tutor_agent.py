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
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator

from core.ai.agents.evaluator_agent import EvaluatorAgent
from core.ai.hint_engine import HintEngine

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
    last_dimension_scores: list[dict] = field(default_factory=list)  # 上次各構面評分
    requested_hint: bool = False  # 學生是否主動要求提示
    # 先前「真的有評分」的作答次數。只拿到提示、沒評分的作答不算——
    # 否則每給一次提示就多佔一次額度，學生還沒用到第 2 層提示就被轉介老師。
    # None 表示呼叫端沒提供，退回用 attempt_number - 1（假設每次都有評分）。
    evaluated_attempts: int | None = None

    @property
    def prior_evaluations(self) -> int:
        if self.evaluated_attempts is not None:
            return self.evaluated_attempts
        return max(self.attempt_number - 1, 0)

    @classmethod
    def from_db(
        cls,
        attempt_number: int,
        last_score_percent: float | None,
        hints_used_count: int,
        pass_threshold: float = 75.0,
        last_dimension_scores: list[dict] | None = None,
        requested_hint: bool = False,
        evaluated_attempts: int | None = None,
    ) -> "StudentContext":
        return cls(
            attempt_number=attempt_number,
            last_score_percent=last_score_percent,
            hints_used_count=hints_used_count,
            pass_threshold=pass_threshold,
            last_dimension_scores=last_dimension_scores or [],
            requested_hint=requested_hint,
            evaluated_attempts=evaluated_attempts,
        )


class TutorAgent:
    """
    教學決策 Agent。

    決策規則：
    ┌─────────────────────────────────┬──────────────┐
    │ 情況                            │ 行動         │
    ├─────────────────────────────────┼──────────────┤
    │ 第一次作答 / 上次只拿到提示沒評分 │ EVALUATE     │
    │ 上次分數 >= 門檻                  │ EVALUATE     │
    │ 接近門檻（差 <10%）且嘗試 <=3 次  │ ENCOURAGE    │
    │ 分數 60~門檻                      │ GIVE_HINT    │
    │ 分數 < 60%，評分過 <= 2 次        │ GIVE_HINT    │
    │ 分數 < 60%，評分過 >= 3 次        │ ESCALATE     │
    └─────────────────────────────────┴──────────────┘

    GIVE_HINT 只是「該給提示了」的決策；要不要真的給、給第幾層，
    由 HintEngine 依教授的 teaching_strategy（max_auto_hints、trigger）決定。
    沒有可給的提示時改為直接評分，所以不會無限給提示。
    """

    def __init__(self, hint_engine: HintEngine | None = None):
        self._evaluator = EvaluatorAgent()
        self._hints = hint_engine or HintEngine()

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

        # 分數 60% 以上但未達門檻 → 給下一層提示；
        # 提示給完（或下一層要學生主動要求）時 HintEngine 回 None，改為評分
        if score >= 60:
            return TutorAction.GIVE_HINT

        # 分數低於 60%：已經被評分 3 次以上仍然很低 → 建議找老師
        if ctx.prior_evaluations <= 2:
            return TutorAction.GIVE_HINT
        return TutorAction.ESCALATE

    async def process_stream(
        self,
        student_answer: str,
        rubric: dict,
        system_prompt: str,
        student_context: StudentContext,
        teaching_strategy: dict | None = None,
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
            hint = await self._hints.next_hint(
                teaching_strategy=teaching_strategy,
                student_answer=student_answer,
                dimension_scores=student_context.last_dimension_scores,
                hints_used=student_context.hints_used_count,
                requested_by_student=student_context.requested_hint,
                rubric=rubric,
            )

            if hint is None:
                # 提示已給完、或此刻不該給（例如下一層要學生主動要求）
                # → 不留下空白，改為直接評分
                logger.info("無可用提示，改為直接評分")
                async for chunk in self._evaluator.evaluate_stream(
                    student_answer=student_answer,
                    rubric=rubric,
                    system_prompt=system_prompt,
                ):
                    yield chunk
                return

            yield {
                "type": "hint",
                "data": {
                    **hint.to_event_data(),
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

