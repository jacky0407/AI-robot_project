"""
錯誤分類偵測（Error Taxonomy Detector）

把「這份作答犯了哪一類常見錯誤」標記出來，寫進 `ai_evaluations.detected_errors`。

為什麼需要它：
  分數只告訴教授「這個學生做得好不好」，錯誤分類才告訴教授
  「全班最常卡在哪一關」——這是課程改進與研究分析真正要的資料。

⚠️ 注意：`ai_tools.error_taxonomy` 這個欄位在 schema 就存在，
但 docs/api.md 從頭到尾沒有定義它的結構。以下結構是本模組提出的，
若教授那邊有不同想法，改這裡的 parse 即可，不影響評分流程。

`error_taxonomy` 的結構：

    [
      {
        "code": "diagnosis_only",
        "label": "僅列診斷名稱",
        "description": "只寫出障礙類別或診斷，沒有描述具體可觀察的行為表現",
        "severity": "high",
        "related_dimension": "功能性描述"
      }
    ]

偵測結果（寫入 detected_errors）：

    [
      {
        "code": "diagnosis_only",
        "label": "僅列診斷名稱",
        "severity": "high",
        "evidence": "「診斷為自閉症」",
        "explanation": "整段只提到診斷，沒有描述小明在情境中的實際表現"
      }
    ]
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from core.ai.llm_utils import invoke_json

logger = logging.getLogger(__name__)

VALID_SEVERITIES = ("high", "medium", "low")
DEFAULT_SEVERITY = "medium"

DETECTOR_SYSTEM_PROMPT = """你是一位學前特殊教育的專業評閱者。

你的任務是對照給定的「常見錯誤清單」，判斷學生的作答犯了其中哪幾項。

嚴格遵守：
1. 只能從清單裡挑，不可以自己發明新的錯誤類型
2. 每一項都必須引用學生作答的原文作為證據，引不出原文就不要標記
3. 沒有犯任何一項就回傳空陣列，不要為了交差硬湊
4. 寧可漏標也不要誤標——誤標會讓教授對系統失去信任

嚴格以 JSON 格式回傳，不得包含 markdown 標記。"""


def parse_error_taxonomy(error_taxonomy: list | None) -> list[dict]:
    """
    把資料庫的 `ai_tools.error_taxonomy` 整理成可用的清單。

    容錯：非 list、元素非 dict、缺 code 或 label 的項目一律跳過。
    """
    if not isinstance(error_taxonomy, list):
        return []

    cleaned: list[dict] = []
    for item in error_taxonomy:
        if not isinstance(item, dict):
            continue
        code = (item.get("code") or "").strip()
        label = (item.get("label") or "").strip()
        if not code or not label:
            continue

        severity = item.get("severity")
        if severity not in VALID_SEVERITIES:
            severity = DEFAULT_SEVERITY

        cleaned.append({
            "code": code,
            "label": label,
            "description": (item.get("description") or "").strip(),
            "severity": severity,
            "related_dimension": item.get("related_dimension") or None,
        })
    return cleaned


def format_taxonomy_for_prompt(taxonomy: list[dict]) -> str:
    """把錯誤清單轉成 Prompt 文字。"""
    lines = []
    for item in taxonomy:
        line = f"- {item['code']}（{item['label']}）"
        if item["description"]:
            line += f"：{item['description']}"
        if item["related_dimension"]:
            line += f"｜相關構面：{item['related_dimension']}"
        lines.append(line)
    return "\n".join(lines)


class ErrorDetector:
    """
    錯誤分類偵測器。

    用法：
        detector = ErrorDetector()
        errors = await detector.detect(answer, tool["error_taxonomy"])

    教授沒定義 error_taxonomy 時直接回空 list，**完全不呼叫 LLM**，
    不會為了一個沒人設定的功能多燒一次 API 額度。
    """

    def __init__(self, llm=None):
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from core.config import get_settings

            settings = get_settings()
            if not settings.gemini_api_key:
                raise ValueError("GEMINI_API_KEY 未設定")
            self._llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model,
                google_api_key=settings.gemini_api_key,
                temperature=0.1,   # 分類要穩定，比評分更低
            )
        return self._llm

    async def detect(
        self,
        student_answer: str,
        error_taxonomy: list | None,
        *,
        dimension_scores: list[dict] | None = None,
    ) -> list[dict]:
        """
        偵測作答中的錯誤類型。

        Args:
            student_answer:   學生作答原文
            error_taxonomy:   ai_tools.error_taxonomy 原始值
            dimension_scores: 各構面評分，讓 AI 知道哪裡失分（選填）
        Returns:
            偵測到的錯誤清單；沒有定義分類或偵測失敗時回空 list
        """
        taxonomy = parse_error_taxonomy(error_taxonomy)
        if not taxonomy:
            return []

        valid_codes = {item["code"]: item for item in taxonomy}

        score_hint = ""
        if dimension_scores:
            score_hint = "\n【各構面得分】\n" + "\n".join(
                f"- {d.get('dimension')}：{d.get('score')}/{d.get('max_score')}"
                f" — {d.get('reason', '')}"
                for d in dimension_scores
            )

        prompt = f"""【常見錯誤清單】
{format_taxonomy_for_prompt(taxonomy)}
{score_hint}

【學生作答】
{student_answer}

請判斷這份作答犯了清單中的哪幾項錯誤。

嚴格以 JSON 格式回傳：
{{
  "detected": [
    {{
      "code": "必須是上面清單裡的 code",
      "evidence": "從學生作答直接引用的原文（不超過50字）",
      "explanation": "為什麼這段構成該項錯誤（1句）"
    }}
  ]
}}

沒有犯任何錯誤時回傳 {{"detected": []}}。"""

        try:
            data = await invoke_json(
                self.llm,
                [
                    SystemMessage(content=DETECTOR_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ],
                label="detect_errors",
                max_attempts=2,   # 錯誤分類不是關鍵路徑，失敗就放棄
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"錯誤分類偵測失敗，略過：{e}")
            return []

        return self._normalize_detected(data.get("detected"), valid_codes)

    @staticmethod
    def _normalize_detected(detected, valid_codes: dict) -> list[dict]:
        """
        過濾 AI 的回傳：丟掉不在清單裡的 code、沒有原文佐證的項目、重複項。

        AI 有時會自創 code 或標記卻引不出原文，這兩種都直接丟掉——
        教授看到的必須是可回溯到原文的標記。
        """
        if not isinstance(detected, list):
            return []

        results: list[dict] = []
        seen: set[str] = set()

        for item in detected:
            if not isinstance(item, dict):
                continue

            code = (item.get("code") or "").strip()
            if code not in valid_codes or code in seen:
                continue

            evidence = (item.get("evidence") or "").strip()
            if not evidence:
                continue

            seen.add(code)
            definition = valid_codes[code]
            results.append({
                "code": code,
                "label": definition["label"],
                "severity": definition["severity"],
                "evidence": evidence,
                "explanation": (item.get("explanation") or "").strip(),
            })

        return results
