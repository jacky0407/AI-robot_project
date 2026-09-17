"""
單元測試：Rubric 格式化工具 (core/ai/rubric_formatter.py)

測試原則：
  - 不需要資料庫或 API，直接跑
  - 確認 Rubric dict 能正確轉成 AI 看得懂的文字格式
"""

import pytest
from core.ai.rubric_formatter import format_rubric_to_text


SAMPLE_RUBRIC = {
    "pass_threshold_percent": 75,
    "dimensions": [
        {
            "name": "功能性描述",
            "weight": 40,
            "levels": [
                {"score": 4, "description": "完整描述優勢與需求，有具體行為觀察"},
                {"score": 3, "description": "描述大致完整，缺乏部分細節"},
                {"score": 2, "description": "描述籠統，缺乏行為觀察"},
                {"score": 1, "description": "未描述或僅列障礙類別"},
            ],
        },
        {
            "name": "去標籤化用語",
            "weight": 20,
            "levels": [
                {"score": 4, "description": "全程使用優勢本位語言"},
                {"score": 1, "description": "大量標籤化用語"},
            ],
        },
    ],
}


class TestFormatRubricToText:

    def test_output_is_string(self):
        result = format_rubric_to_text(SAMPLE_RUBRIC)
        assert isinstance(result, str)

    def test_contains_dimension_names(self):
        result = format_rubric_to_text(SAMPLE_RUBRIC)
        assert "功能性描述" in result
        assert "去標籤化用語" in result

    def test_contains_score_levels(self):
        result = format_rubric_to_text(SAMPLE_RUBRIC)
        assert "4分" in result
        assert "3分" in result
        assert "2分" in result
        assert "1分" in result

    def test_contains_descriptions(self):
        result = format_rubric_to_text(SAMPLE_RUBRIC)
        assert "完整描述優勢與需求" in result
        assert "優勢本位語言" in result

    def test_contains_weight(self):
        """權重資訊要顯示在構面標題裡"""
        result = format_rubric_to_text(SAMPLE_RUBRIC)
        assert "40%" in result

    def test_scores_ordered_high_to_low(self):
        """分數應該從高到低排列（讓 AI 先看到最高標準）"""
        result = format_rubric_to_text(SAMPLE_RUBRIC)
        idx_4 = result.index("4分")
        idx_1 = result.index("1分")
        assert idx_4 < idx_1

    def test_empty_dimensions(self):
        """空的 rubric 不應該 crash"""
        result = format_rubric_to_text({"dimensions": []})
        assert isinstance(result, str)

    def test_missing_weight_graceful(self):
        """沒有 weight 欄位時不應該 crash"""
        rubric = {
            "dimensions": [
                {
                    "name": "測試構面",
                    "levels": [{"score": 4, "description": "很好"}],
                }
            ]
        }
        result = format_rubric_to_text(rubric)
        assert "測試構面" in result


# ==============================================================================
# normalize_rubric / build_default_levels
# （修正 KNOWN_GAPS #5：max_score 是配分，不是量表上限）
# ==============================================================================

from core.ai.rubric_formatter import (
    build_default_levels,
    normalize_rubric,
    DEFAULT_SCALE_MAX,
)

# 資料庫 ai_tools.rubric_criteria 的真實格式（無 levels）
DB_RUBRIC_CRITERIA = [
    {"dimension": "客觀事實辨識", "max_score": 40, "description": "能準確擷取具體行為與數據"},
    {"dimension": "推論與假設區分", "max_score": 30, "description": "能將推論與觀察分開標示"},
    {"dimension": "缺漏資訊提問", "max_score": 30, "description": "能指出案例未提供的關鍵訊息"},
]


class TestBuildDefaultLevels:

    def test_always_returns_four_levels(self):
        """不論規準說明多長，一律展開成 4 級量表"""
        levels = build_default_levels("能準確擷取具體行為與數據")
        assert len(levels) == DEFAULT_SCALE_MAX

    def test_scores_are_four_to_one(self):
        levels = build_default_levels("測試")
        assert sorted(lv["score"] for lv in levels) == [1, 2, 3, 4]

    def test_descriptions_are_distinct(self):
        """各級描述必須不同，否則 AI 無從判斷該給幾分"""
        levels = build_default_levels("能準確擷取具體行為與數據")
        descriptions = [lv["description"] for lv in levels]
        assert len(set(descriptions)) == DEFAULT_SCALE_MAX

    def test_includes_original_description(self):
        levels = build_default_levels("能準確擷取具體行為與數據")
        assert all("能準確擷取具體行為與數據" in lv["description"] for lv in levels)

    def test_empty_description_graceful(self):
        levels = build_default_levels("")
        assert len(levels) == DEFAULT_SCALE_MAX
        assert all(lv["description"] for lv in levels)


class TestNormalizeRubric:

    def test_max_score_becomes_weight_not_scale(self):
        """
        回歸測試：max_score=40 不可以產生 40 個等級。
        它是配分，應該變成 weight，等級固定 4 級。
        """
        result = normalize_rubric(DB_RUBRIC_CRITERIA, pass_threshold_percent=70)
        first = result["dimensions"][0]

        assert first["weight"] == 40
        assert len(first["levels"]) == DEFAULT_SCALE_MAX

    def test_keeps_pass_threshold(self):
        result = normalize_rubric(DB_RUBRIC_CRITERIA, pass_threshold_percent=70)
        assert result["pass_threshold_percent"] == 70

    def test_dimension_key_maps_to_name(self):
        """資料庫用 dimension，標準結構用 name"""
        result = normalize_rubric(DB_RUBRIC_CRITERIA, 70)
        names = [d["name"] for d in result["dimensions"]]
        assert names == ["客觀事實辨識", "推論與假設區分", "缺漏資訊提問"]

    def test_custom_levels_are_preserved(self):
        """教授自訂了 levels 就不要覆蓋"""
        custom = [{
            "dimension": "功能性描述",
            "max_score": 50,
            "levels": [
                {"score": 5, "description": "非常好"},
                {"score": 1, "description": "待加強"},
            ],
        }]
        result = normalize_rubric(custom, 75)
        assert len(result["dimensions"][0]["levels"]) == 2
        assert result["dimensions"][0]["levels"][0]["score"] == 5

    def test_no_weights_falls_back_to_equal(self):
        """全部沒配分時改為等權重，避免加權時分母為 0"""
        result = normalize_rubric(
            [{"dimension": "A", "description": "x"}, {"dimension": "B", "description": "y"}],
            75,
        )
        assert [d["weight"] for d in result["dimensions"]] == [1, 1]

    def test_weight_stays_int_when_integral(self):
        """權重要保持整數，避免格式化成『權重 40.0%』"""
        result = normalize_rubric(DB_RUBRIC_CRITERIA, 70)
        assert isinstance(result["dimensions"][0]["weight"], int)

    def test_empty_criteria_graceful(self):
        result = normalize_rubric([], 75)
        assert result["dimensions"] == []

    def test_none_criteria_graceful(self):
        result = normalize_rubric(None, 75)
        assert result["dimensions"] == []

    def test_output_is_formattable(self):
        """正規化後應該可以直接餵給 format_rubric_to_text"""
        result = normalize_rubric(DB_RUBRIC_CRITERIA, 70)
        text = format_rubric_to_text(result)
        assert "客觀事實辨識" in text
        assert "40%" in text          # 不是 40.0%
        assert "4分" in text and "1分" in text
