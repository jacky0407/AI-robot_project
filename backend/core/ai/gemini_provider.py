"""
Gemini AI Provider 實作（使用新版 google-genai SDK）

使用 Google Gemini API 實作 BaseAIProvider 的評分與提示生成邏輯。
"""

import json
import logging
from typing import AsyncGenerator

from google import genai
from google.genai import types

from core.config import get_settings
from core.ai.base import BaseAIProvider, EvaluationResult

logger = logging.getLogger(__name__)

# Gemini 要求的 JSON 回應格式說明（寫在 Prompt 裡）
EVALUATION_JSON_SCHEMA = """
請嚴格以下列 JSON 格式回傳，不得包含任何其他文字或 markdown 符號：
{
  "dimension_scores": [
    {
      "name": "構面名稱",
      "score": 數字,
      "max_score": 數字,
      "reason": "評分理由（1-2句）",
      "evidence": "從學生作答摘錄的原文佐證片段"
    }
  ],
  "overall_feedback": "整體回饋（2-3句，正向、具體、不浮誇）",
  "confidence": 0到1之間的浮點數
}
"""


class GeminiProvider(BaseAIProvider):

    def __init__(self):
        self._client = None  # 懶惰初始化，第一次呼叫 API 時才建立
        self.model_id = "gemini-2.0-flash"

    @property
    def client(self):
        """第一次存取時才初始化 Gemini Client，避免啟動時就需要 API Key"""
        if self._client is None:
            settings = get_settings()
            if not settings.gemini_api_key:
                raise ValueError(
                    "GEMINI_API_KEY 未設定。請在 backend/.env 填入您的 API Key。"
                )
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    def _build_evaluation_prompt(
        self,
        student_answer: str,
        rubric_text: str,
        system_prompt: str,
    ) -> str:
        return f"""{system_prompt}

你的任務是根據以下評分規準，客觀評分學生的作答。

【評分規準】
{rubric_text}

【學生作答】
{student_answer}

{EVALUATION_JSON_SCHEMA}
"""

    async def evaluate(
        self,
        student_answer: str,
        rubric_text: str,
        system_prompt: str,
    ) -> EvaluationResult:
        prompt = self._build_evaluation_prompt(student_answer, rubric_text, system_prompt)

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            return self._parse_response(response.text)

        except Exception as e:
            logger.error(f"Gemini evaluation failed: {e}")
            raise

    async def evaluate_stream(
        self,
        student_answer: str,
        rubric_text: str,
        system_prompt: str,
    ) -> AsyncGenerator[dict, None]:
        """
        取得完整評分後逐一 yield 各構面（模擬串流效果）。
        """
        result = await self.evaluate(student_answer, rubric_text, system_prompt)

        for dim in result.dimension_scores:
            yield {"type": "dimension_score", "data": dim}

        yield {
            "type": "score_complete",
            "data": {
                "total_score": result.total_score,
                "max_total_score": result.max_total_score,
                "percentage": result.percentage,
                "overall_feedback": result.overall_feedback,
                "confidence": result.confidence,
                "needs_teacher_review": result.needs_teacher_review,
                "passed": False,  # 由呼叫方依 pass_threshold 計算後補上
            },
        }

    async def generate_hint(
        self,
        student_answer: str,
        hint_template: str,
        dimension_scores: list[dict],
    ) -> str:
        weakest = min(
            dimension_scores,
            key=lambda d: d["score"] / d["max_score"],
            default=None,
        )
        weakest_info = (
            f"學生在「{weakest['name']}」這個構面得分最低（{weakest['score']}/{weakest['max_score']}）"
            if weakest else ""
        )

        prompt = f"""你是一位溫和的教學引導者。
{weakest_info}

請根據以下提示模板，生成一段針對學生作答的個人化引導問題（不超過 100 字，不給出答案）：

提示模板：{hint_template}

學生作答：{student_answer}

只回傳引導問題文字，不要加任何前綴。"""

        response = await self.client.aio.models.generate_content(
            model=self.model_id,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.5),
        )
        return response.text.strip()

    def _parse_response(self, raw_json: str) -> EvaluationResult:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini JSON: {e}\nRaw: {raw_json}")
            raise ValueError("AI 回傳格式錯誤，請稍後再試")

        dim_scores = data.get("dimension_scores", [])
        total = sum(d["score"] for d in dim_scores)
        max_total = sum(d["max_score"] for d in dim_scores)
        percentage = (total / max_total * 100) if max_total > 0 else 0
        confidence = float(data.get("confidence", 0.8))

        return EvaluationResult(
            dimension_scores=dim_scores,
            total_score=total,
            max_total_score=max_total,
            percentage=round(percentage, 1),
            overall_feedback=data.get("overall_feedback", ""),
            confidence=confidence,
            needs_teacher_review=confidence < 0.7,
        )
