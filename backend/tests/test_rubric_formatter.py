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
