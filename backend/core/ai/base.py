"""
AI 服務抽象層（Abstract AI Provider）

設計原則：
  - 教學邏輯（Rubric 評分、分層提示）不綁定任何特定 AI 廠商
  - 若未來需要從 Gemini 換成 OpenAI，只需新增一個 Provider class
  - 目前預設使用 GeminiProvider
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator


@dataclass
class EvaluationResult:
    """AI 評分結果的標準格式（不論使用哪個 AI 廠商，都回傳此格式）"""
    dimension_scores: list[dict]   # [{name, score, max_score, reason, evidence}]
    total_score: float
    max_total_score: float
    percentage: float
    overall_feedback: str
    confidence: float              # 0~1，AI 自我評估的信心值
    needs_teacher_review: bool


class BaseAIProvider(ABC):
    """所有 AI Provider 都必須繼承此 class 並實作以下方法"""

    @abstractmethod
    async def evaluate(
        self,
        student_answer: str,
        rubric_text: str,
        system_prompt: str,
    ) -> EvaluationResult:
        """
        依 Rubric 評分學生作答。
        Args:
            student_answer: 學生提交的作答原文
            rubric_text:    格式化後的 Rubric 文字（由 rubric_formatter 產生）
            system_prompt:  教授為這支機器人設定的角色說明
        Returns:
            EvaluationResult
        """
        pass

    @abstractmethod
    async def evaluate_stream(
        self,
        student_answer: str,
        rubric_text: str,
        system_prompt: str,
    ) -> AsyncGenerator[dict, None]:
        """
        依 Rubric 評分，以 AsyncGenerator 逐步產出結果（供 SSE 使用）。
        每次 yield 一個 dict，對應一個構面或最終總結。
        """
        pass

    @abstractmethod
    async def generate_hint(
        self,
        student_answer: str,
        hint_template: str,
        dimension_scores: list[dict],
    ) -> str:
        """
        根據學生作答與分數，生成個人化的提示文字。
        Args:
            student_answer:  學生作答
            hint_template:   教授設定的提示模板（含 {student_answer} 等佔位符）
            dimension_scores: 各構面評分，幫助 AI 知道弱點在哪
        Returns:
            個人化提示文字
        """
        pass
