"""
資料字典（docs/DATA_DICTIONARY.md，DAT-003）要跟匯出欄位同步：
匯出新增欄位卻沒寫進資料字典，這個測試會失敗。
"""

from pathlib import Path

from api.export import COMMON_COLUMNS

DICTIONARY = Path(__file__).resolve().parents[2] / "docs" / "DATA_DICTIONARY.md"
EXTRA_COLUMNS = ["research_id", "student_name", "student_email", "answer_text"]


def test_dictionary_exists():
    assert DICTIONARY.exists(), "缺少 docs/DATA_DICTIONARY.md"


def test_every_export_column_is_documented():
    text = DICTIONARY.read_text(encoding="utf-8")
    missing = [c for c in COMMON_COLUMNS + EXTRA_COLUMNS if f"| `{c}` |" not in text]
    assert not missing, f"這些匯出欄位沒有寫進資料字典：{missing}"
