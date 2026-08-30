"""
個資偵測模組 (PII Detection)

在學生提交作答之前，偵測是否含有疑似個人識別資訊（PII）。
偵測到時回傳警告，但不強制阻擋（記錄事件供管理員稽核）。

需求書 PRI-002：
  敏感資料偵測：對明顯身分證、電話、電子郵件、地址、出生日期等
  先阻擋並要求修改；說明自動偵測不保證完整。
"""

import re
from dataclasses import dataclass


@dataclass
class PIIDetectionResult:
    has_risk: bool
    detected_types: list[str]
    warning_message: str


# 偵測規則：(類型名稱, 正規表達式)
PII_PATTERNS = [
    ("taiwan_id", r"\b[A-Z][12]\d{8}\b"),                              # 台灣身分證
    ("phone_tw", r"\b09\d{2}[-\s]?\d{3}[-\s]?\d{3}\b"),               # 台灣手機
    ("phone_landline", r"\b0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}\b"),      # 市話
    ("email", r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"),  # Email
    ("address_tw", r"[縣市區鄉鎮村里路街巷弄號樓]{2,}"),               # 台灣地址片段
    ("birth_date", r"\b\d{3}年\d{1,2}月\d{1,2}日\b|\b\d{4}/\d{2}/\d{2}\b"),  # 日期
]

TYPE_DISPLAY_NAMES = {
    "taiwan_id":    "身分證字號",
    "phone_tw":     "手機號碼",
    "phone_landline": "市話號碼",
    "email":        "電子郵件",
    "address_tw":   "地址資訊",
    "birth_date":   "出生日期",
}


def detect_pii(text: str) -> PIIDetectionResult:
    """
    偵測文字中是否含有疑似個資。
    Args:
        text: 學生的作答原文
    Returns:
        PIIDetectionResult
    """
    detected = []

    for type_key, pattern in PII_PATTERNS:
        if re.search(pattern, text):
            detected.append(type_key)

    if not detected:
        return PIIDetectionResult(
            has_risk=False,
            detected_types=[],
            warning_message="",
        )

    display_names = [TYPE_DISPLAY_NAMES.get(t, t) for t in detected]

    return PIIDetectionResult(
        has_risk=True,
        detected_types=detected,
        warning_message=(
            f"⚠️ 您的作答可能包含個人識別資訊（{', '.join(display_names)}）。"
            "請確認案例內容均為「虛構個案」，不包含任何真實當事人的個人資料，"
            "再重新提交。（系統自動偵測不保證完整，請自行確認。）"
        ),
    )
