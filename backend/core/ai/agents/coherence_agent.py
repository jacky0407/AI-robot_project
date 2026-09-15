"""
跨步驟整合 Agent（Coherence Checker Agent）

對應需求書 BLD-007：
「模組最後可呼叫專屬檢核機器人，跨步驟檢查現況、需求、目標、
策略與評量的一致性，並連回問題所在步驟。」

當學生完成 IEP 模組所有步驟後呼叫此 Agent，
它會讀取所有步驟的作答，檢查前後邏輯是否一貫，
並指出哪些步驟需要修改。
"""

import json
import logging
from dataclasses import dataclass
from typing import AsyncGenerator

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

from core.config import get_settings

logger = logging.getLogger(__name__)


# ── 預設的跨步驟一致性檢核規則 ──────────────────────────────────────────
# 每條規則描述「哪個步驟的哪個面向」應該和「哪個步驟的哪個面向」相呼應
DEFAULT_COHERENCE_CHECKS = [
    {
        "name": "優勢與目標對應",
        "description": (
            "步驟 1 描述的學生優勢，應在步驟 3 的 IEP 目標中被充分運用。"
            "若目標完全忽略了優勢，代表目標設計可能過於缺陷導向。"
        ),
        "source_step_order": 1,
        "source_aspect": "學生優勢描述",
        "target_step_order": 3,
        "target_aspect": "IEP 長期目標",
    },
    {
        "name": "需求與策略對應",
        "description": (
            "步驟 2 確認的特殊需求，應在步驟 5 的教學策略中有具體回應。"
            "每個重要需求都應有至少一個對應策略。"
        ),
        "source_step_order": 2,
        "source_aspect": "特殊需求清單",
        "target_step_order": 5,
        "target_aspect": "教學策略說明",
    },
    {
        "name": "目標與評量對應",
        "description": (
            "步驟 3 的 IEP 目標應可被步驟 7 的評量方式具體測量。"
            "目標若無法量化或觀察，評量設計就沒有意義。"
        ),
        "source_step_order": 3,
        "source_aspect": "IEP 目標（含標準）",
        "target_step_order": 7,
        "target_aspect": "評量方式與標準",
    },
]

COHERENCE_SYSTEM_PROMPT = """你是一位學前特殊教育的 IEP 品質審查專家。
你的任務是跨步驟檢查學生的 IEP 作答是否前後一致、邏輯連貫。

審查原則：
1. 聚焦在「前後呼應」，而非重新評分單一步驟的品質
2. 若發現不一致，清楚說明哪個步驟的哪個內容與另一步驟矛盾或脫節
3. 回饋具體且可操作，讓學生知道要去哪個步驟修改什麼
4. 若整體一致性良好，也要明確肯定

嚴格以 JSON 格式回傳，不得包含 markdown 標記。"""


@dataclass
class StepAnswer:
    """單一步驟的作答資料"""
    step_order: int
    step_title: str
    content: str            # 學生的作答原文
    passed: bool            # 是否通過評分


@dataclass
class CoherenceIssue:
    """一個跨步驟不一致的問題"""
    check_name: str
    is_ok: bool
    problem: str            # 問題描述（is_ok=False 時才有）
    suggestion: str         # 修改建議
    go_to_step_order: int   # 建議去修改的步驟編號


@dataclass
class CoherenceResult:
    """整體一致性檢核結果"""
    overall_coherent: bool
    issues: list[CoherenceIssue]
    strengths: list[str]    # 做得好的地方
    summary: str            # 整體摘要


class CoherenceAgent:
    """
    跨步驟整合 Agent。

    使用方式：
      result = await agent.check(step_answers)

    串流使用方式（SSE）：
      async for chunk in agent.check_stream(step_answers):
          ...
    """

    def __init__(self, checks: list[dict] | None = None):
        self._llm = None
        self.checks = checks or DEFAULT_COHERENCE_CHECKS

    @property
    def llm(self) -> ChatGoogleGenerativeAI:
        if self._llm is None:
            settings = get_settings()
            if not settings.gemini_api_key:
                raise ValueError("GEMINI_API_KEY 未設定")
            self._llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=settings.gemini_api_key,
                temperature=0.3,
            )
        return self._llm

    async def check_stream(
        self,
        step_answers: list[StepAnswer],
    ) -> AsyncGenerator[dict, None]:
        """
        執行跨步驟一致性檢核，逐條規則 yield 結果。

        SSE 事件序列：
          coherence_start   → 開始檢核，告知共幾條規則
          coherence_check   → 每條規則的檢核結果
          coherence_complete → 所有規則完成，含整體摘要
        """
        # 建立步驟索引（step_order → StepAnswer）
        step_map = {s.step_order: s for s in step_answers}

        # 篩出有效的檢核規則（兩個步驟都有作答）
        valid_checks = [
            c for c in self.checks
            if c["source_step_order"] in step_map
            and c["target_step_order"] in step_map
        ]

        yield {
            "type": "coherence_start",
            "data": {
                "total_checks": len(valid_checks),
                "total_steps": len(step_answers),
            },
        }

        issues: list[CoherenceIssue] = []
        strengths: list[str] = []

        # 逐條規則進行檢核
        for i, rule in enumerate(valid_checks):
            source = step_map[rule["source_step_order"]]
            target = step_map[rule["target_step_order"]]

            result = await self._check_single_rule(rule, source, target)
            issues.append(result)

            if result.is_ok:
                strengths.append(result.check_name)

            yield {
                "type": "coherence_check",
                "data": {
                    "check_name": result.check_name,
                    "is_ok": result.is_ok,
                    "problem": result.problem,
                    "suggestion": result.suggestion,
                    "go_to_step_order": result.go_to_step_order,
                    "progress": f"{i + 1}/{len(valid_checks)}",
                },
            }

        # 整體一致性判斷
        overall_coherent = all(issue.is_ok for issue in issues)
        failed_issues = [iss for iss in issues if not iss.is_ok]

        yield {
            "type": "coherence_complete",
            "data": {
                "overall_coherent": overall_coherent,
                "passed_checks": len(strengths),
                "failed_checks": len(failed_issues),
                "strengths": strengths,
                "issues": [
                    {
                        "check_name": iss.check_name,
                        "problem": iss.problem,
                        "suggestion": iss.suggestion,
                        "go_to_step_order": iss.go_to_step_order,
                    }
                    for iss in failed_issues
                ],
                "summary": self._build_summary(overall_coherent, failed_issues),
            },
        }

    async def _check_single_rule(
        self,
        rule: dict,
        source: StepAnswer,
        target: StepAnswer,
    ) -> CoherenceIssue:
        """對單一規則進行 AI 檢核。"""
        prompt = f"""請檢查以下兩個 IEP 步驟之間的一致性：

【檢核規則】
{rule['description']}

【步驟 {source.step_order}：{source.step_title}】（關注：{rule['source_aspect']}）
{source.content}

【步驟 {target.step_order}：{target.step_title}】（關注：{rule['target_aspect']}）
{target.content}

請嚴格以 JSON 格式回傳：
{{
  "is_ok": true 或 false,
  "problem": "若 is_ok=false，描述具體的不一致之處（引用原文）；若 is_ok=true，填空字串",
  "suggestion": "若 is_ok=false，給出可操作的修改建議；若 is_ok=true，填空字串"
}}"""

        try:
            response = await self.llm.ainvoke([
                SystemMessage(content=COHERENCE_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            data = json.loads(response.content)
        except Exception as e:
            logger.warning(f"Coherence check failed for rule '{rule['name']}': {e}")
            data = {
                "is_ok": False,
                "problem": "檢核失敗，建議人工複核",
                "suggestion": "請教師手動確認兩步驟的一致性",
            }

        return CoherenceIssue(
            check_name=rule["name"],
            is_ok=bool(data.get("is_ok", False)),
            problem=data.get("problem", ""),
            suggestion=data.get("suggestion", ""),
            go_to_step_order=target.step_order,
        )

    def _build_summary(
        self,
        overall_coherent: bool,
        failed_issues: list[CoherenceIssue],
    ) -> str:
        if overall_coherent:
            return "你的 IEP 各步驟前後一致，邏輯連貫，恭喜完成整份 IEP 的撰寫！"

        steps_to_fix = sorted({iss.go_to_step_order for iss in failed_issues})
        step_list = "、".join(f"步驟 {s}" for s in steps_to_fix)
        return (
            f"發現 {len(failed_issues)} 處跨步驟不一致，"
            f"建議優先修改 {step_list}，"
            "修改後再重新執行整合檢核。"
        )
