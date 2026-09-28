"""
單元測試：個資偵測模組 (core/privacy.py)

測試原則：
  - 不需要資料庫或 API，直接跑
  - 覆蓋「應偵測到」和「不應誤判」兩種情境
"""

import pytest
from core.privacy import detect_pii


# ──────────────────────────────────────────
# 應該偵測到個資的情境
# ──────────────────────────────────────────

class TestShouldDetectPII:

    def test_taiwan_id(self):
        result = detect_pii("該生身分證字號為 A123456789")
        assert result.has_risk is True
        assert "taiwan_id" in result.detected_types

    def test_mobile_phone(self):
        result = detect_pii("家長手機：0912-345-678")
        assert result.has_risk is True
        assert "phone_tw" in result.detected_types

    def test_mobile_phone_no_dash(self):
        result = detect_pii("聯絡電話 0912345678")
        assert result.has_risk is True
        assert "phone_tw" in result.detected_types

    def test_email(self):
        result = detect_pii("請寄信至 parent@example.com 確認")
        assert result.has_risk is True
        assert "email" in result.detected_types

    def test_birth_date_chinese(self):
        result = detect_pii("出生日期：113年5月3日")
        assert result.has_risk is True
        assert "birth_date" in result.detected_types

    def test_birth_date_slash(self):
        result = detect_pii("生日：2014/05/03")
        assert result.has_risk is True
        assert "birth_date" in result.detected_types

    def test_multiple_pii_types(self):
        """同時含有多種個資，全部都要偵測到"""
        result = detect_pii("小明 A123456789，手機 0987654321，email: test@test.com")
        assert result.has_risk is True
        assert len(result.detected_types) >= 3

    def test_warning_message_not_empty(self):
        """偵測到個資時，警告訊息不能是空字串"""
        result = detect_pii("身分證 A123456789")
        assert result.has_risk is True
        assert len(result.warning_message) > 0


# ──────────────────────────────────────────
# 不應誤判正常教學內容
# ──────────────────────────────────────────

class TestShouldNotFalsePositive:

    def test_normal_educational_text(self):
        result = detect_pii("小明具備良好的溝通能力，能主動與同儕互動，在自由遊戲時段表現積極。")
        assert result.has_risk is False

    def test_rubric_text(self):
        result = detect_pii("功能性描述需包含學生的優勢行為、特殊需求與具體觀察，避免標籤化用語。")
        assert result.has_risk is False

    def test_iep_goal_text(self):
        result = detect_pii("IEP 目標：學生能在 3 次嘗試中有 2 次成功完成自我照顧任務。")
        assert result.has_risk is False

    def test_empty_string(self):
        result = detect_pii("")
        assert result.has_risk is False

    def test_short_number_not_phone(self):
        """短數字不應被誤判為手機號碼"""
        result = detect_pii("得分：85 分，共嘗試 3 次。")
        assert result.has_risk is False
