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
