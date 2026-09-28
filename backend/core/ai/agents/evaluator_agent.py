"""
評分 Agent（Evaluator Agent）

將單次 Gemini 呼叫升級為多步驟 Agent，逐一完成：
  1. 分析作答結構
  2. 逐構面評分（每次只評一個，聚焦且精確）
  3. 擷取原文佐證
  4. 整合最終回饋

優點：
  - 每步驟可獨立驗證，比一次性評分更準確
  - 前端可以接收更細緻的 SSE 進度事件
  - 未來可以替換單個步驟的工具，不影響整體架構
"""

import json
import logging
from typing import AsyncGenerator

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import get_settings
from core.ai.base import EvaluationResult
from core.ai.rubric_formatter import format_rubric_to_text, DEFAULT_SCALE_MAX

logger = logging.getLogger(__name__)

EVALUATOR_SYSTEM_PROMPT = """你是一位學前特殊教育的專業評分助理。
你的工作是依照給定的 Rubric 評分規準，對學生的作答進行客觀、公正的評分。

評分原則：
1. 每次只評一個構面，聚焦分析
2. 引用學生原文作為評分佐證，不自行補充
3. 回饋語言正向、具體，指出實際做得好的地方與需要改善處
4. 信心值（confidence）反映您對自己評分的把握程度：
   - 0.9+ = 作答清楚，評分明確
   - 0.7~0.9 = 作答有些模糊，但可以判斷
   - < 0.7 = 作答不足或難以判斷，建議教師複核

嚴格以 JSON 格式回傳，不得加入任何 markdown 標記。"""


def compute_weighted_percentage(dimension_scores: list[dict]) -> float:
    """
    依構面權重計算總得分百分比。

    每個構面先換算成得分率（score / max_score），再依 weight 加權平均。
    這樣 max_score 可以是任意量表（4 級、5 級），weight 才是配分。

    若所有構面都沒有 weight（例如 GeminiProvider 的舊路徑），
    退回原本的「總分 / 總滿分」算法，維持向下相容。
    """
    total_weight = sum(float(d.get("weight") or 0) for d in dimension_scores)

    if total_weight > 0:
        acc = 0.0
        for d in dimension_scores:
            max_score = float(d.get("max_score") or DEFAULT_SCALE_MAX) or DEFAULT_SCALE_MAX
            weight = float(d.get("weight") or 0)
            acc += (float(d.get("score", 0)) / max_score) * weight
        return acc / total_weight * 100

    total = sum(float(d.get("score", 0)) for d in dimension_scores)
    max_total = sum(float(d.get("max_score") or DEFAULT_SCALE_MAX) for d in dimension_scores)
    return (total / max_total * 100) if max_total > 0 else 0.0


class EvaluatorAgent:
    """
    多步驟評分 Agent。
    使用 LangChain + Gemini 的 Function Calling 能力，
    將評分拆成有順序的步驟執行。
    """

    def __init__(self):
        self._llm = None

    @property
    def llm(self) -> ChatGoogleGenerativeAI:
        if self._llm is None:
            settings = get_settings()
            if not settings.gemini_api_key:
                raise ValueError("GEMINI_API_KEY 未設定")
            self._llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=settings.gemini_api_key,
                temperature=0.2,
            )
        return self._llm

    async def evaluate(
        self,
        student_answer: str,
        rubric: dict,
        system_prompt: str,
    ) -> EvaluationResult:
        """
        完整執行評分流程（非串流版本）。

        Args:
            student_answer: 學生作答原文
            rubric: Rubric 資料（dict 格式，含 dimensions）
            system_prompt: 教授設定的機器人角色說明

        Returns:
            EvaluationResult
        """
        dimensions = rubric.get("dimensions", [])
        pass_threshold = rubric.get("pass_threshold_percent", 75)
        rubric_text = format_rubric_to_text(rubric)

        # Step 1：逐構面評分
        dimension_scores = []
        for dim in dimensions:
            score_data = await self._score_dimension(
                student_answer=student_answer,
                dimension=dim,
                system_prompt=system_prompt,
            )
            dimension_scores.append(score_data)

        # Step 2：整合最終回饋
        final = await self._synthesize(
            student_answer=student_answer,
            dimension_scores=dimension_scores,
            rubric_text=rubric_text,
            pass_threshold=pass_threshold,
            system_prompt=system_prompt,
        )

        return final

    async def evaluate_stream(
        self,
        student_answer: str,
        rubric: dict,
        system_prompt: str,
    ) -> AsyncGenerator[dict, None]:
        """
        串流版本：每完成一個構面評分就 yield 一次。
        前端可以逐步顯示各構面分數出現的過程。
        """
        dimensions = rubric.get("dimensions", [])
        pass_threshold = rubric.get("pass_threshold_percent", 75)
        rubric_text = format_rubric_to_text(rubric)

        yield {"type": "analysis_start", "data": {"total_dimensions": len(dimensions)}}

        # 逐構面評分，每完成一個就 yield
        dimension_scores = []
        for i, dim in enumerate(dimensions):
            score_data = await self._score_dimension(
                student_answer=student_answer,
                dimension=dim,
                system_prompt=system_prompt,
            )
            dimension_scores.append(score_data)
            yield {
                "type": "dimension_score",
                "data": {**score_data, "progress": f"{i + 1}/{len(dimensions)}"},
            }

        # 整合最終結果
        final = await self._synthesize(
            student_answer=student_answer,
            dimension_scores=dimension_scores,
            rubric_text=rubric_text,
            pass_threshold=pass_threshold,
            system_prompt=system_prompt,
        )

        yield {
            "type": "score_complete",
            "data": {
                "total_score": final.total_score,
                "max_total_score": final.max_total_score,
                "percentage": final.percentage,
                "passed": final.percentage >= pass_threshold,
                "overall_feedback": final.overall_feedback,
                "confidence": final.confidence,
                "needs_teacher_review": final.needs_teacher_review,
                "dimension_scores": final.dimension_scores,
            },
        }

    async def _score_dimension(
        self,
        student_answer: str,
        dimension: dict,
        system_prompt: str,
    ) -> dict:
        """對單一構面進行評分，返回結構化結果。"""
        dim_name = dimension.get("name", "未命名構面")
        levels = dimension.get("levels", [])
        max_score = max(lv["score"] for lv in levels) if levels else DEFAULT_SCALE_MAX

        # 組裝單一構面的評分 prompt
        levels_text = "\n".join(
            f"  {lv['score']}分：{lv['description']}"
            for lv in sorted(levels, key=lambda x: x["score"], reverse=True)
        )

        prompt = f"""{system_prompt}

請只針對「{dim_name}」這個構面進行評分。

【{dim_name} 的評分等級】
{levels_text}

【學生作答】
{student_answer}

請嚴格以 JSON 格式回傳，不得包含任何其他文字：
{{
  "dimension": "{dim_name}",
  "score": 數字（{min(lv['score'] for lv in levels) if levels else 1}~{max_score}）,
  "max_score": {max_score},
  "reason": "評分理由（1-2句，說明為何給這個分數）",
  "evidence": "從學生作答直接引用的原文片段（不超過80字）"
}}"""

        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=EVALUATOR_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            scored = json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning(f"構面 {dim_name} JSON 解析失敗，使用預設值")
            scored = {
                "dimension": dim_name,
                "score": 1,
                "max_score": max_score,
                "reason": "評分失敗，請教師複核",
                "evidence": "",
            }

        # AI 不負責決定權重，一律由 Rubric 設定帶入，並確保量表上限正確
        scored["weight"] = dimension.get("weight", 0)
        scored["max_score"] = max_score
        scored.setdefault("dimension", dim_name)
        return scored

    async def _synthesize(
        self,
        student_answer: str,
        dimension_scores: list[dict],
        rubric_text: str,
        pass_threshold: float,
        system_prompt: str,
    ) -> EvaluationResult:
        """整合所有構面評分，生成整體回饋。"""
        percentage = compute_weighted_percentage(dimension_scores)
        has_weights = any(float(d.get("weight") or 0) > 0 for d in dimension_scores)

        if has_weights:
            # 有配分 → 總分即加權後的百分制得分，滿分固定 100
            total = round(percentage, 1)
            max_total = 100.0
        else:
            total = sum(float(d.get("score", 0)) for d in dimension_scores)
            max_total = sum(float(d.get("max_score") or DEFAULT_SCALE_MAX) for d in dimension_scores)

        # 組裝各構面摘要給 AI 參考
        scores_summary = "\n".join(
            f"- {d['dimension']}：{d['score']}/{d['max_score']} 分 — {d.get('reason', '')}"
            for d in dimension_scores
        )

        prompt = f"""{system_prompt}

以下是學生各構面的評分結果：
{scores_summary}

【學生作答】
{student_answer}

請根據以上評分結果，生成整體回饋，並評估您的信心值。
嚴格以 JSON 格式回傳：
{{
  "overall_feedback": "整體回饋（2-3句，正向且具體，引用實際作答，指出最重要的1個改善方向）",
  "confidence": 0到1之間的浮點數（你對這次評分結果的把握程度）
}}"""

        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=EVALUATOR_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            synthesis = json.loads(response.content)
        except Exception:
            synthesis = {
                "overall_feedback": "整體評分完成，請參考各構面回饋。",
                "confidence": 0.6,
            }

        confidence = float(synthesis.get("confidence", 0.8))

        return EvaluationResult(
            dimension_scores=dimension_scores,
            total_score=total,
            max_total_score=max_total,
            percentage=round(percentage, 1),
            overall_feedback=synthesis.get("overall_feedback", ""),
            confidence=confidence,
            needs_teacher_review=confidence < 0.7,
        )
