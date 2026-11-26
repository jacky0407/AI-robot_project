"""
單元測試：資料匯出（api/export.py、DAT-001、開會紀錄 #49 第 7 項）

  - 只限教授；助教、學生 403
  - 研究用：沒有姓名與 Email、用匿名研究編號；只含已同意且未撤回的學生；作答原文預設不匯出、勾選時遮罩個資
  - 教學用：含姓名與 Email
  - 只匯出這門課的學生、這門課指派的模組；可依模組與日期篩選
  - CSV 有 BOM（Excel 中文不亂碼）；每次匯出寫一筆稽核紀錄，寫入失敗不擋匯出
"""

import csv
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from api import export
from api.export import mask_pii
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

PROF = CurrentUser(id="prof-1", role="owner")
COURSE = "c0000000-0000-4000-8000-000000000001"
OTHER_COURSE = "c0000000-0000-4000-8000-000000000002"
MOD = "b0000000-0000-4000-8000-000000000001"
MOD_OTHER = "b0000000-0000-4000-8000-000000000002"


def attempt(uid, n, *, at="2026-11-05T10:00:00Z", module=MOD, content="小明在積木角專注二十分鐘", score=80):
    return {
        "id": f"a-{uid}-{n}-{module[-1]}", "user_id": uid, "module_id": module, "step_id": "s1",
        "attempt_number": n, "status": "passed", "tutor_action": "evaluate", "created_at": at,
        "user_input_content": content,
        "module_steps": {"step_order": 1, "step_title": "案例整理", "pass_score": 70, "ai_tools": {"title": "教練"}},
        "ai_evaluations": [{"total_score": score, "confidence": 0.9,
                            "dimension_scores": [{"dimension": "客觀", "score": 3, "max_score": 4}],
                            "detected_errors": [{"code": "E1", "label": "標籤"}]}],
        "teacher_reviews": [{"decision": "modify", "final_score": 85, "is_published": True}],
        "prompt_logs": [{"hint_level": 1}, {"hint_level": 2}],
    }


def make_db():
    return InMemoryDB(
        courses=[{"id": COURSE, "title": "課程"}, {"id": OTHER_COURSE, "title": "別的課"}],
        course_modules=[{"course_id": COURSE, "module_id": MOD, "learning_modules": {"title": "IEP 模組"}},
                        {"course_id": COURSE, "module_id": MOD_OTHER, "learning_modules": {"title": "第二模組"}}],
        course_members=[
            {"course_id": COURSE, "user_id": "u-agree", "status": "active",
             "profiles": {"full_name": "王小美", "email": "amy@x.tw", "role": "student"}},
            {"course_id": COURSE, "user_id": "u-no", "status": "active",
             "profiles": {"full_name": "李小斌", "email": "ben@x.tw", "role": "student"}},
            {"course_id": COURSE, "user_id": "u-withdrawn", "status": "removed",
             "profiles": {"full_name": "陳小貓", "email": "cat@x.tw", "role": "student"}},
            {"course_id": COURSE, "user_id": "prof-1", "status": "active",
             "profiles": {"full_name": "吳教授", "email": "prof@x.tw", "role": "owner"}},
            {"course_id": OTHER_COURSE, "user_id": "u-other", "status": "active",
             "profiles": {"full_name": "別班", "email": "o@x.tw", "role": "student"}},
        ],
        research_consents=[
            {"user_id": "u-agree", "anonymous_research_id": "SPED-aaaa1111", "is_consented": True, "withdrawn_at": None},
            {"user_id": "u-no", "anonymous_research_id": "SPED-bbbb2222", "is_consented": False, "withdrawn_at": None},
            {"user_id": "u-withdrawn", "anonymous_research_id": "SPED-cccc3333", "is_consented": True,
             "withdrawn_at": "2026-11-10"},
        ],
        step_attempts=[
            attempt("u-agree", 1), attempt("u-agree", 2, at="2026-11-09T10:00:00Z"),
            attempt("u-agree", 1, module=MOD_OTHER),
            attempt("u-no", 1), attempt("u-withdrawn", 1), attempt("prof-1", 1), attempt("u-other", 1),
        ],
        export_logs=[],
    )


@pytest.fixture(autouse=True)
def as_professor():
    main.app.dependency_overrides[get_current_user] = lambda: PROF
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def post(kind, db, **body):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(export, "get_supabase", return_value=db):
        return client.post(f"/api/export/{kind}", json={"course_id": COURSE, **body})


def rows_of(res):
    text = res.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:])))


class TestPermissions:

    @pytest.mark.parametrize("role", ["assistant", "student"])
    def test_only_professor_can_export(self, role):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="x", role=role)
        for kind in ("research", "teaching"):
            assert post(kind, make_db()).status_code == 403

    def test_unknown_course_is_404(self):
        res = post("research", make_db(), course_id="c0000000-0000-4000-8000-0000000000ff")
        assert res.json()["detail"]["code"] == "COURSE_NOT_FOUND"


class TestResearch:

    def test_only_consented_students_and_no_identity(self):
        db = make_db()
        res = post("research", db)
        assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
        rows = rows_of(res)

        assert {r["research_id"] for r in rows} == {"SPED-aaaa1111"}
        assert len(rows) == 3
        text = res.content.decode("utf-8")
        for secret in ("王小美", "amy@x.tw", "李小斌", "陳小貓", "SPED-cccc3333", "別班", "吳教授"):
            assert secret not in text
        assert "answer_text" not in rows[0]
        assert rows[0]["ai_dimensions"] == "客觀:3/4" and rows[0]["hints_used"] == "2"
        assert rows[0]["teacher_final_score"] == "85"

    def test_content_is_masked_when_included(self):
        db = make_db()
        db.rows("step_attempts")[0]["user_input_content"] = "家長電話 0912-345-678，信箱 mom@example.com"
        rows = rows_of(post("research", db, include_content=True))
        texts = [r["answer_text"] for r in rows]
        assert any("[已遮罩]" in t for t in texts)
        assert not any("0912" in t or "mom@example.com" in t for t in texts)

    def test_module_and_date_filters(self):
        rows = rows_of(post("research", make_db(), module_id=MOD, date_from="2026-11-08"))
        assert len(rows) == 1 and rows[0]["attempt_number"] == "2"

    def test_module_not_in_course_is_404(self):
        res = post("research", make_db(), module_id="b0000000-0000-4000-8000-0000000000ff")
        assert res.json()["detail"]["code"] == "MODULE_NOT_IN_COURSE"

    def test_audit_log_is_written(self):
        db = make_db()
        post("research", db, include_content=True)
        log = db.rows("export_logs")[0]
        assert log["kind"] == "research" and log["exported_by"] == "prof-1"
        assert log["row_count"] == 3 and log["params"]["include_content"] is True

    def test_audit_failure_does_not_block_export(self):
        db = make_db()
        db.errors[("export_logs", "insert")] = RuntimeError('relation "export_logs" does not exist')
        assert post("research", db).status_code == 200


class TestTeaching:

    def test_includes_names_of_course_students_only(self):
        rows = rows_of(post("teaching", make_db(), include_content=True))
        names = {r["student_name"] for r in rows}
        assert names == {"王小美", "李小斌", "陳小貓"}        # 教授本人與別班不在內
        assert rows[0]["answer_text"]                      # 教學用不遮罩

    def test_header_row_counts(self):
        res = post("teaching", make_db())
        assert res.headers["x-export-row-count"] == "5"
        assert 'filename="teaching-export-' in res.headers["content-disposition"]


def test_mask_pii_keeps_normal_text():
    assert mask_pii("小明在積木角專注二十分鐘") == "小明在積木角專注二十分鐘"
    assert "[已遮罩]" in mask_pii("身分證 A123456789")


# ──────────────────────────────────────────
# #83 聊天室逐則訊息、Excel、最新的教師判定
# ──────────────────────────────────────────

def chat_db():
    db = make_db()
    step = {"step_order": 2, "step_title": "家長晤談"}
    db.tables["chat_messages"] = [
        {"id": "m1", "user_id": "u-agree", "module_id": MOD, "step_id": "s2", "role": "student",
         "content": "請問孩子早上的作息？電話 0912-345-678", "routing": None, "attempt_id": None,
         "created_at": "2026-11-05T09:00:00Z", "module_steps": step},
        {"id": "m2", "user_id": "u-agree", "module_id": MOD, "step_id": "s2", "role": "bot", "bot_name": "IEP 小幫手",
         "content": "你覺得要先觀察什麼？", "attempt_id": None, "created_at": "2026-11-05T09:00:05Z", "module_steps": step,
         "routing": {"by": "router", "reason": "問 IEP 寫法", "answer_mode": "guide", "times_asked": 1,
                     "sources": ["IEP 指引・第 2 段"]}},
        {"id": "m3", "user_id": "u-agree", "module_id": MOD, "step_id": "s2", "role": "bot", "bot_name": "評分老師",
         "content": "評分：80 分", "attempt_id": "a-u-agree-2-1", "created_at": "2026-11-09T10:00:00Z",
         "module_steps": step, "routing": {"by": "evaluate", "reason": "送出評分"}},
        {"id": "m4", "user_id": "u-no", "module_id": MOD, "step_id": "s2", "role": "student", "content": "沒同意的",
         "routing": None, "attempt_id": None, "created_at": "2026-11-05T09:00:00Z", "module_steps": step},
        {"id": "m5", "user_id": "u-other", "module_id": MOD, "step_id": "s2", "role": "student", "content": "別班的",
         "routing": None, "attempt_id": None, "created_at": "2026-11-05T09:00:00Z", "module_steps": step},
    ]
    return db


class TestChatExport:

    def test_research_chat_only_consented_and_masked(self):
        res = post("research", chat_db(), dataset="chat_messages", include_content=True)
        rows = rows_of(res)

        assert {r["research_id"] for r in rows} == {"SPED-aaaa1111"}
        assert "student_name" not in rows[0]
        assert "0912-345-678" not in rows[0]["message_text"] and "[已遮罩]" in rows[0]["message_text"]
        assert res.headers["X-Export-Row-Count"] == "3"
        assert 'research-export-chat-' in res.headers["Content-Disposition"]

    def test_chat_columns(self):
        rows = rows_of(post("research", chat_db(), dataset="chat_messages"))
        tutor, evaluation = rows[1], rows[2]
        assert (tutor["routed_by"], tutor["is_tutor"], tutor["answer_mode"], tutor["times_asked"], tutor["sources"]) == \
            ("router", "True", "guide", "1", "IEP 指引・第 2 段")
        assert evaluation["routed_by"] == "evaluate" and evaluation["evaluation_attempt_number"] == "2"
        assert rows[0]["role"] == "student" and rows[0]["is_tutor"] == ""
        assert "message_text" not in rows[0]          # 預設不匯出內容

    def test_teaching_chat_has_names_of_course_students_only(self):
        rows = rows_of(post("teaching", chat_db(), dataset="chat_messages"))
        assert {r["student_name"] for r in rows} == {"王小美", "李小斌"}

    def test_missing_chat_table_means_no_rows(self):
        db = make_db()
        db.errors[("chat_messages", "select")] = RuntimeError("relation chat_messages does not exist")
        res = post("research", db, dataset="chat_messages")
        assert res.status_code == 200 and rows_of(res) == []


class TestXlsx:

    def test_xlsx_has_attempts_and_chat_sheets(self):
        from openpyxl import load_workbook

        res = post("research", chat_db(), format="xlsx")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert res.headers["X-Export-Row-Count"] == "3" and res.headers["X-Export-Chat-Count"] == "3"
        wb = load_workbook(io.BytesIO(res.content), read_only=True)
        assert wb.sheetnames == ["作答", "聊天訊息"]
        attempts = list(wb["作答"].iter_rows(values_only=True))
        chats = list(wb["聊天訊息"].iter_rows(values_only=True))
        assert attempts[0][0] == "research_id" and len(attempts) == 4
        assert chats[0][0] == "research_id" and len(chats) == 4

    def test_xlsx_export_is_audited(self):
        db = chat_db()
        post("teaching", db, format="xlsx")
        assert db.rows("export_logs")[0]["params"]["format"] == "xlsx"


def test_latest_teacher_review_is_exported():
    db = make_db()
    for a in db.tables["step_attempts"]:
        a["teacher_reviews"] = [
            {"decision": "modify", "final_score": 60, "is_published": True, "reviewed_at": "2026-11-10T00:00:00Z"},
            {"decision": "override", "final_score": 90, "is_published": True, "reviewed_at": "2026-11-12T00:00:00Z"},
            {"decision": "accept_ai", "final_score": 70, "is_published": False, "reviewed_at": "2026-11-11T00:00:00Z"},
        ]
    row = rows_of(post("research", db))[0]
    assert (row["teacher_decision"], row["teacher_final_score"], row["teacher_reviewed_at"]) == \
        ("override", "90", "2026-11-12T00:00:00Z")
