"""
Rubric 格式化與正規化工具

兩個職責：
  1. normalize_rubric()     — 把資料庫的 rubric_criteria 轉成 Agent 需要的標準結構
  2. format_rubric_to_text() — 把標準結構轉成 AI 看得懂的純文字 Prompt 片段
"""

# 預設量表上限：沒有自訂 levels 時，一律用 4 級量表評分
DEFAULT_SCALE_MAX = 4


def _tidy_weight(value) -> float | int:
    """權重若為整數就回傳 int，避免格式化成 `40.0%`。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def build_default_levels(description: str) -> list[dict]:
    """
    教授只填了構面名稱與規準說明、沒有逐級描述時，
    自動展開成固定的 4 級量表。

    ⚠️ 不可以用 range(1, max_score+1) 展開：
    rubric_criteria 的 max_score 是「配分（權重）」不是「量表上限」，
    40 分的構面會產生 40 個描述完全相同的等級，AI 無從判斷。

    Args:
        description: 教授填寫的規準說明
    Returns:
        4 個等級，由高到低
    """
    desc = (description or "").strip()
    suffix = f"：{desc}" if desc else ""

    return [
        {"score": 4, "description": f"完全符合規準{suffix}"},
        {"score": 3, "description": f"大致符合規準，僅少數細節不足{suffix}"},
        {"score": 2, "description": f"部分符合規準，缺漏明顯{suffix}"},
        {"score": 1, "description": f"幾乎未符合規準{suffix}"},
    ]


def normalize_rubric(
    rubric_criteria: list[dict] | None,
    pass_threshold_percent: float,
) -> dict:
    """
    把資料庫 `ai_tools.rubric_criteria` 轉成 EvaluatorAgent 需要的結構。

    資料庫格式（由教授在工具建構器填寫）：
        [{"dimension": "客觀事實辨識", "max_score": 40, "description": "..."}]
        └─ max_score 是「配分」，代表這個構面佔總分的權重

    轉換後格式：
        {
          "pass_threshold_percent": 70,
          "dimensions": [
            {"name": "客觀事實辨識", "weight": 40, "levels": [4級量表]}
          ]
        }

    規則：
      - `levels` 若教授有自訂就沿用，否則展開成固定 4 級量表
      - `weight` 取自 max_score；若全部構面都沒配分，改為等權重

    Args:
        rubric_criteria:        資料庫原始 JSONB
        pass_threshold_percent: 通過門檻（取自 module_steps.pass_score）
    Returns:
        標準化後的 rubric dict
    """
    dimensions: list[dict] = []

    for i, item in enumerate(rubric_criteria or [], start=1):
        levels = item.get("levels") or build_default_levels(item.get("description", ""))

        # 相容兩種欄位命名：資料庫用 max_score，標準結構用 weight
        raw_weight = item.get("weight", item.get("max_score", 0))

        dimensions.append({
            "name": item.get("dimension") or item.get("name") or f"構面{i}",
            "weight": _tidy_weight(raw_weight),
            "levels": levels,
        })

    # 完全沒有設定配分 → 視為等權重，避免加權計算時分母為 0
    if dimensions and all(float(d["weight"]) <= 0 for d in dimensions):
        for d in dimensions:
            d["weight"] = 1

    return {
        "pass_threshold_percent": pass_threshold_percent,
        "dimensions": dimensions,
    }


def format_rubric_to_text(rubric: dict) -> str:
    """
    Args:
        rubric: 標準化後的 Rubric（見 normalize_rubric）
    Returns:
        純文字格式的 Rubric，例如：
            【構面1：功能性描述（權重 30%）】
            4分：完整描述學生的優勢與需求，有具體行為觀察
            3分：描述大致完整，但缺乏部分細節
    """
    lines = []
    dimensions = rubric.get("dimensions", [])

    for i, dim in enumerate(dimensions, start=1):
        name = dim.get("name", f"構面{i}")
        weight = dim.get("weight", "")
        weight_str = f"（權重 {_tidy_weight(weight)}%）" if weight else ""
        lines.append(f"【構面{i}：{name}{weight_str}】")

        # 從高分到低分排列
        levels = sorted(dim.get("levels", []), key=lambda x: x["score"], reverse=True)
        for level in levels:
            lines.append(f"  {level['score']}分：{level['description']}")

        lines.append("")  # 構面之間空一行

    return "\n".join(lines).strip()
