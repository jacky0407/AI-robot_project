"""
評分工具集（Evaluation Tools）

定義評分 Agent 可以呼叫的工具函式。
每個工具負責評分流程的一個步驟，確保每步驟品質可控。
"""

from langchain_core.tools import tool


@tool
def analyze_answer_structure(answer: str, required_elements: list[str]) -> dict:
    """
    分析學生作答的結構完整性。
    檢查作答是否包含必要的元素（如：優勢描述、需求說明、具體行為觀察等）。

    Args:
        answer: 學生的作答原文
        required_elements: 此步驟要求必須出現的元素清單

    Returns:
        {
          "has_all_elements": bool,
          "missing": ["缺少的元素"],
          "present": ["已包含的元素"],
          "completeness_score": 0~1 的浮點數
        }
    """
    present = []
    missing = []

    for element in required_elements:
        # 簡易關鍵字比對（AI 會在後續步驟做更深入分析）
        if element in answer:
            present.append(element)
        else:
            missing.append(element)

    completeness = len(present) / len(required_elements) if required_elements else 1.0

    return {
        "has_all_elements": len(missing) == 0,
        "missing": missing,
        "present": present,
        "completeness_score": round(completeness, 2),
    }


@tool
def score_single_dimension(
    answer: str,
    dimension_name: str,
    dimension_levels: list[dict],
) -> dict:
    """
    針對單一 Rubric 構面評分。
    每次只評一個構面，讓 AI 可以聚焦，提高準確率。

    Args:
        answer: 學生的作答原文
        dimension_name: 構面名稱（如「功能性描述」）
        dimension_levels: 此構面的評分等級清單
            [{"score": 4, "description": "..."}, {"score": 3, "description": "..."}, ...]

    Returns:
        {
          "dimension": "構面名稱",
          "score": 數字,
          "max_score": 數字,
          "reason": "評分理由",
          "evidence": "從作答摘錄的原文佐證"
        }
    """
    # 此工具實際上是給 LangChain Agent 的工具描述
    # Agent 會根據 description 決定如何呼叫，真正的 AI 推理在 Agent 內部完成
    # 這裡提供結構定義讓 Agent 知道輸出格式
    max_score = max(level["score"] for level in dimension_levels) if dimension_levels else 4
    return {
        "dimension": dimension_name,
        "score": 0,           # Agent 填入
        "max_score": max_score,
        "reason": "",         # Agent 填入
        "evidence": "",       # Agent 填入
    }


@tool
def extract_key_evidence(answer: str, dimension_name: str, scored_level: int) -> str:
    """
    從學生作答中，擷取與特定構面評分最相關的原文片段作為佐證。
    確保回饋引用的是學生自己寫的內容，而非 AI 自己編造。

    Args:
        answer: 學生的作答原文
        dimension_name: 要擷取佐證的構面名稱
        scored_level: 已評定的分數（影響擷取哪些片段）

    Returns:
        原文佐證字串（不超過 150 字）
    """
    # 此工具的實際執行由 Agent 透過 LLM 完成
    return ""


@tool
def synthesize_feedback(
    dimension_scores: list[dict],
    pass_threshold_percent: float,
) -> dict:
    """
    整合所有構面的評分結果，生成最終回饋。

    Args:
        dimension_scores: 所有構面的評分結果清單
        pass_threshold_percent: 通過門檻（百分比，如 75.0）

    Returns:
        {
          "total_score": 數字,
          "max_total_score": 數字,
          "percentage": 浮點數,
          "passed": bool,
          "overall_feedback": "整體回饋文字",
          "confidence": 0~1 的浮點數,
          "needs_teacher_review": bool
        }
    """
    total = sum(d.get("score", 0) for d in dimension_scores)
    max_total = sum(d.get("max_score", 4) for d in dimension_scores)
    percentage = (total / max_total * 100) if max_total > 0 else 0

    return {
        "total_score": total,
        "max_total_score": max_total,
        "percentage": round(percentage, 1),
        "passed": percentage >= pass_threshold_percent,
        "overall_feedback": "",   # Agent 填入
        "confidence": 0.8,        # Agent 填入
        "needs_teacher_review": False,  # Agent 根據 confidence 決定
    }
