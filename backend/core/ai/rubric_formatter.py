"""
Rubric 格式化與正規化工具

兩個職責：
  1. normalize_rubric()     — 把資料庫的 rubric_criteria 轉成 Agent 需要的標準結構
  2. format_rubric_to_text() — 把標準結構轉成 AI 看得懂的純文字 Prompt 片段
"""

# 預設量表上限：沒有自訂 levels 也沒指定 scale_type 時，用 4 級量表
DEFAULT_SCALE_MAX = 4

# 各量表類型的上限分數（對應 api.md 的 scale_type）
SCALE_MAX_SCORES = {
    "4_point": 4,
    "5_point": 5,
    "100_point": 100,
    "pass_fail": 1,
}

DEFAULT_SCALE_TYPE = "4_point"

# 各級距的通用描述前綴，由高到低
_LEVEL_PREFIXES = {
    4: ["完全符合規準", "大致符合規準，僅少數細節不足", "部分符合規準，缺漏明顯", "幾乎未符合規準"],
    5: ["完全符合規準且有額外深度", "完全符合規準", "大致符合規準，少數細節不足",
        "部分符合規準，缺漏明顯", "幾乎未符合規準"],
}


def _tidy_weight(value) -> float | int:
    """權重若為整數就回傳 int，避免格式化成 `40.0%`。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def scale_max_score(scale_type: str | None) -> int:
    """取得量表上限分數，未知的 scale_type 退回預設 4 級。"""
    return SCALE_MAX_SCORES.get(scale_type or DEFAULT_SCALE_TYPE, DEFAULT_SCALE_MAX)


def build_default_levels(
    description: str,
    scale_type: str | None = DEFAULT_SCALE_TYPE,
) -> list[dict]:
    """
    教授只填了構面名稱與規準說明、沒有逐級描述時，依量表類型自動展開級距。

    ⚠️ 不可以用 range(1, max_score+1) 展開：
    rubric_criteria 的 max_score 是「配分（權重）」不是「量表上限」，
    40 分的構面會產生 40 個描述完全相同的等級，AI 無從判斷。

    各量表的處理方式：
      4_point / 5_point → 展開成 4 / 5 個描述互異的等級
      pass_fail         → 達成 / 未達成 兩級
      100_point         → 不展開等級（回傳空 list），
                          由 _score_dimension 直接要求 AI 給 0~100 分

    Args:
        description: 教授填寫的規準說明
        scale_type:  量表類型
    Returns:
        等級清單，由高到低；100_point 回傳空 list
    """
    desc = (description or "").strip()
    suffix = f"：{desc}" if desc else ""
    scale = scale_type or DEFAULT_SCALE_TYPE

    if scale == "100_point":
        return []

    if scale == "pass_fail":
        return [
            {"score": 1, "description": f"達成規準{suffix}"},
            {"score": 0, "description": f"未達成規準{suffix}"},
        ]

    max_score = scale_max_score(scale)
    prefixes = _LEVEL_PREFIXES.get(max_score, _LEVEL_PREFIXES[DEFAULT_SCALE_MAX])

    return [
        {"score": max_score - i, "description": f"{prefix}{suffix}"}
        for i, prefix in enumerate(prefixes)
    ]


def normalize_rubric(
    rubric_criteria: list[dict] | None,
    pass_threshold_percent: float,
    scale_type: str | None = DEFAULT_SCALE_TYPE,
) -> dict:
    """
    把資料庫 `ai_tools.rubric_criteria` 轉成 EvaluatorAgent 需要的結構。

    資料庫格式（由教授在工具建構器填寫）：
        [{"dimension": "客觀事實辨識", "max_score": 40, "description": "..."}]
        └─ max_score 是「配分」，代表這個構面佔總分的權重

    轉換後格式：
        {
          "pass_threshold_percent": 70,
          "scale_type": "4_point",
          "dimensions": [
            {"name": "客觀事實辨識", "weight": 40, "max_score": 4,
             "levels": [4級量表], "description": "..."}
          ]
        }

    規則：
      - `levels` 若教授有自訂就沿用，否則依 scale_type 展開
      - `max_score` 是量表上限（評分級距），`weight` 才是配分，兩者不同
      - `weight` 取自 max_score 欄位；若全部構面都沒配分，改為等權重

    Args:
        rubric_criteria:        資料庫原始 JSONB
        pass_threshold_percent: 通過門檻（取自 module_steps.pass_score）
        scale_type:             量表類型（取自 ai_tools.scale_type）
    Returns:
        標準化後的 rubric dict
    """
    scale = scale_type or DEFAULT_SCALE_TYPE
    dimensions: list[dict] = []

    for i, item in enumerate(rubric_criteria or [], start=1):
        levels = item.get("levels") or build_default_levels(item.get("description", ""), scale)

        # 量表上限：教授自訂 levels 時取其最大值，否則依 scale_type
        max_score = max((lv["score"] for lv in levels), default=scale_max_score(scale))

        # 相容兩種欄位命名：資料庫用 max_score 表示配分，標準結構用 weight
        raw_weight = item.get("weight", item.get("max_score", 0))

        dimensions.append({
            "name": item.get("dimension") or item.get("name") or f"構面{i}",
            "weight": _tidy_weight(raw_weight),
            "max_score": max_score,
            "levels": levels,
            "description": item.get("description", ""),
        })

    # 完全沒有設定配分 → 視為等權重，避免加權計算時分母為 0
    if dimensions and all(float(d["weight"]) <= 0 for d in dimensions):
        for d in dimensions:
            d["weight"] = 1

    return {
        "pass_threshold_percent": pass_threshold_percent,
        "scale_type": scale,
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
        if levels:
            for level in levels:
                lines.append(f"  {level['score']}分：{level['description']}")
        else:
            # 100_point 量表沒有離散等級，直接給評分依據
            max_score = dim.get("max_score", 100)
            desc = dim.get("description", "")
            lines.append(f"  0~{max_score} 分連續計分" + (f"，評分依據：{desc}" if desc else ""))

        lines.append("")  # 構面之間空一行

    return "\n".join(lines).strip()
