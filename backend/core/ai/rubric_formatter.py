"""
Rubric 格式化工具

將資料庫中的 Rubric 結構（dict 格式）轉換成
AI 能清楚理解的純文字格式，作為 Prompt 的一部分。
"""


def format_rubric_to_text(rubric: dict) -> str:
    """
    Args:
        rubric: 從資料庫撈出的 Rubric 資料，格式如下：
            {
              "scale_type": "4_point",
              "pass_threshold_percent": 75,
              "dimensions": [
                {
                  "name": "功能性描述",
                  "weight": 30,
                  "levels": [
                    {"score": 4, "description": "..."},
                    {"score": 3, "description": "..."},
                    ...
                  ]
                }
              ]
            }
    Returns:
        純文字格式的 Rubric，例如：
            【構面一：功能性描述（權重 30%）】
            4分：完整描述學生的優勢與需求，有具體行為觀察
            3分：描述大致完整，但缺乏部分細節
            ...
    """
    lines = []
    dimensions = rubric.get("dimensions", [])

    for i, dim in enumerate(dimensions, start=1):
        name = dim.get("name", f"構面{i}")
        weight = dim.get("weight", "")
        weight_str = f"（權重 {weight}%）" if weight else ""
        lines.append(f"【構面{i}：{name}{weight_str}】")

        # 從高分到低分排列
        levels = sorted(dim.get("levels", []), key=lambda x: x["score"], reverse=True)
        for level in levels:
            lines.append(f"  {level['score']}分：{level['description']}")

        lines.append("")  # 構面之間空一行

    return "\n".join(lines).strip()
