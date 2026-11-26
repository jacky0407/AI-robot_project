"""
#84 跨步驟結構化資料傳遞（core/step_forward.py 與各端點）

全部 Mock：抽出欄位的 AI 換成 AsyncMock，資料庫用假的或記憶體版。
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from api import practice, review
from core import step_forward
from core.ai.memory import _prior_content
from core.auth import CurrentUser, get_current_user
from core.step_forward import (
    extract_and_save,
    forwarded_from_rows,
    merged_fields,
    parse_fields,
    set_teacher_fields,
)
from tests.inmemory_db import InMemoryDB

FIELDS = [{"label": "優勢", "description": "孩子能做到的事"}, {"label": "需求"}]


# ──────────────────────────────────────────
# 規則
# ──────────────────────────────────────────

class TestRules:

    def test_parse_fields_accepts_old_string_list_and_dedupes(self):
        fields = parse_fields(["優勢", {"label": "需求", "description": "要支持的地方"}, "優勢", "  ", 3])
        assert [(f.key, f.label, f.description) for f in fields] == [("優勢", "優勢", ""), ("需求", "需求", "要支持的地方")]

    def test_parse_fields_empty(self):
        assert parse_fields(None) == [] and parse_fields([]) == []

    def test_teacher_value_wins(self):
        values, source = merged_fields({"forward": {"ai_fields": {"優勢": "AI 版", "需求": "AI 需求"},
                                                    "teacher_fields": {"優勢": "老師版"}}})
        assert values == {"優勢": "老師版", "需求": "AI 需求"} and source == "mixed"

    def test_source_labels(self):
        assert merged_fields({"forward": {"ai_fields": {"優勢": "a"}}})[1] == "ai"
        assert merged_fields({"forward": {"ai_fields": {"優勢": "a"}, "teacher_fields": {"優勢": "b"}}})[1] == "teacher"
        assert merged_fields({}) == ({}, "")

    def test_teacher_edit_keeps_ai_original(self):
        structured = set_teacher_fields({"ai_feedback": "x", "forward": {"ai_fields": {"優勢": "AI"}}},
                                        {"優勢": "老師"}, "prof-1", {"優勢": "優勢"})
        assert structured["forward"]["ai_fields"] == {"優勢": "AI"}
        assert structured["forward"]["teacher_fields"] == {"優勢": "老師"}
        assert structured["ai_feedback"] == "x"


@pytest.mark.asyncio
class TestExtract:

    async def test_extractor_maps_fields_and_drops_empty(self):
        with patch.object(step_forward, "invoke_json", AsyncMock(return_value={"優勢": "喜歡積木", "需求": None})), \
             patch.object(step_forward, "chat_llm", lambda t, **kw: object()):
            values = await step_forward.ForwardExtractor().extract("作答", parse_fields(FIELDS))
        assert values == {"優勢": "喜歡積木", "需求": None}

    async def test_saves_and_keeps_other_structured_data(self):
        db = InMemoryDB(step_attempts=[{"id": "a1", "structured_data": {}}])
        with patch.object(step_forward.extractor, "extract", AsyncMock(return_value={"優勢": "喜歡積木"})):
            values = await extract_and_save(db, "a1", {"pass_forward_keys": FIELDS}, "作答", {"ai_feedback": "好"})
        saved = db.rows("step_attempts")[0]["structured_data"]
        assert values == {"優勢": "喜歡積木"}
        assert saved["ai_feedback"] == "好" and saved["forward"]["ai_fields"] == {"優勢": "喜歡積木"}
        assert saved["forward"]["labels"] == {"優勢": "優勢", "需求": "需求"}

    async def test_no_fields_configured_means_no_ai_call(self):
        extract = AsyncMock()
        with patch.object(step_forward.extractor, "extract", extract):
            assert await extract_and_save(InMemoryDB(), "a1", {"pass_forward_keys": []}, "作答") is None
        extract.assert_not_awaited()

    async def test_teacher_version_is_never_overwritten(self):
        extract = AsyncMock()
        with patch.object(step_forward.extractor, "extract", extract):
            result = await extract_and_save(InMemoryDB(), "a1", {"pass_forward_keys": FIELDS}, "作答",
                                            {"forward": {"teacher_fields": {"優勢": "老師"}}})
        assert result is None
        extract.assert_not_awaited()

    async def test_ai_failure_is_ignored(self):
        db = InMemoryDB(step_attempts=[{"id": "a1", "structured_data": {}}])
        with patch.object(step_forward.extractor, "extract", AsyncMock(side_effect=RuntimeError("timeout"))):
            assert await extract_and_save(db, "a1", {"pass_forward_keys": FIELDS}, "作答") is None
        assert db.rows("step_attempts")[0]["structured_data"] == {}


# ──────────────────────────────────────────
# 後面的關卡讀前面的欄位
# ──────────────────────────────────────────

def row(step_id, order, n, *, status="passed", ai=None, teacher=None):
    return {"step_id": step_id, "attempt_number": n, "status": status, "user_input_content": "全文" * 200,
            "module_steps": {"step_order": order, "step_title": f"第{order}關", "pass_score": 70, "pass_forward_keys": FIELDS},
            "teacher_reviews": [],
            "structured_data": {"forward": {"ai_fields": ai or {}, **({"teacher_fields": teacher} if teacher else {})}}}


class TestForwarded:

    def test_passed_version_preferred_and_later_steps_ignored(self):
        rows = [
            row("s1", 1, 1, ai={"優勢": "第一次"}),
            row("s1", 1, 2, status="revision_required", ai={"優勢": "第二次沒過"}),
            row("s2", 2, 1, ai={"需求": "第二關"}),
            row("s3", 3, 1, ai={"優勢": "自己這關"}),
        ]
        out = forwarded_from_rows(rows, before_order=3, passed=lambda r: r["status"] == "passed")
        assert [(s.step_order, s.attempt_number) for s in out] == [(1, 1), (2, 1)]
        assert out[0].fields == [{"key": "優勢", "label": "優勢", "value": "第一次", "source": "ai"}]

    def test_steps_without_fields_are_skipped(self):
        assert forwarded_from_rows([row("s1", 1, 1)], 3, lambda r: True) == []

    def test_memory_uses_fields_instead_of_full_text(self):
        r = row("s1", 1, 2, ai={"優勢": "喜歡積木", "需求": "轉換活動"}, teacher={"需求": "轉換時需要視覺提示"})
        text = _prior_content(r, r["module_steps"])
        assert "全文" not in text
        assert "第 2 次作答" in text and "部分教師確認" in text
        assert "需求：轉換時需要視覺提示" in text

    def test_memory_falls_back_to_full_text(self):
        r = row("s1", 1, 1)
        assert _prior_content(r, r["module_steps"]).startswith("全文")


# ──────────────────────────────────────────
# 端點
# ──────────────────────────────────────────

class TestSubmit:

    @pytest.fixture(autouse=True)
    def student(self, monkeypatch):
        from tests import test_practice_api as t
        main.app.dependency_overrides[get_current_user] = lambda: t.STUDENT
        monkeypatch.setattr(practice, "check_step_access", lambda db, user, step: t.StepAccess(mode="course"))
        monkeypatch.setattr(practice, "consume_quota", lambda db, access: None)
        monkeypatch.setattr(practice, "check_step_unlocked", lambda db, user, step: None)
        yield t
        main.app.dependency_overrides.pop(get_current_user, None)

    def submit(self, t, score):
        fake = t.FakeSupabase({**t.STEP_ROW, "pass_forward_keys": FIELDS}, prev_attempts=[])
        responses = [t.dim_response("客觀事實辨識", score), t.dim_response("推論與假設區分", score),
                     t.dim_response("缺漏資訊提問", score), t.SYNTH_RESPONSE]
        extract = AsyncMock(return_value={"優勢": "喜歡積木", "需求": None})
        with patch.object(step_forward.extractor, "extract", extract):
            _, events = t.submit(fake, responses)
        return fake, events, extract

    def test_passing_extracts_fields(self, student):
        fake, events, extract = self.submit(student, 4)
        assert dict(events)["forward_extracted"]["fields"] == {"優勢": "喜歡積木", "需求": None}
        saved = [c["payload"] for c in fake.find("step_attempts", "update") if "structured_data" in c["payload"]]
        assert saved[-1]["structured_data"]["forward"]["ai_fields"]["優勢"] == "喜歡積木"

    def test_failing_does_not_extract(self, student):
        _, events, extract = self.submit(student, 1)
        assert "forward_extracted" not in dict(events)
        extract.assert_not_awaited()


class TestReviewAndApi:

    @pytest.fixture(autouse=True)
    def teacher(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="prof-1", role="owner")
        yield
        main.app.dependency_overrides.pop(get_current_user, None)

    def db(self, forward=None):
        return InMemoryDB(step_attempts=[{
            "id": "a1", "user_id": "s1", "module_id": "m1", "user_input_content": "作答",
            "structured_data": {"forward": forward} if forward else {},
            "module_steps": {"pass_forward_keys": FIELDS, "pass_mode": "teacher_review", "pass_score": 70,
                             "ai_tools": {"rubric_criteria": [], "system_prompt": ""}},
        }], teacher_reviews=[], audit_logs=[])

    def put(self, db, fields):
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(review, "get_supabase", return_value=db):
            return client.put("/api/review/submissions/a1/forward", json={"fields": fields})

    def test_teacher_correction(self):
        db = self.db({"ai_fields": {"優勢": "AI 版"}})
        res = self.put(db, {"優勢": "老師確認版"})
        assert res.status_code == 200
        field = next(f for f in res.json()["data"]["fields"] if f["key"] == "優勢")
        assert (field["ai_value"], field["teacher_value"]) == ("AI 版", "老師確認版")

    def test_unknown_field_is_rejected(self):
        res = self.put(self.db(), {"目標": "x"})
        assert res.status_code == 400 and res.json()["detail"]["code"] == "UNKNOWN_FIELD"

    def test_students_cannot_edit(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="s1", role="student")
        assert self.put(self.db(), {"優勢": "x"}).status_code == 403

    def test_teacher_review_pass_triggers_extraction(self, monkeypatch):
        db = self.db()
        monkeypatch.setattr(review, "apply_locked_version", lambda db, step: step)
        monkeypatch.setattr(review, "refresh_learner_profile", lambda *a, **k: None)
        extract = AsyncMock(return_value={"優勢": "老師複核後抽出"})
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(review, "get_supabase", return_value=db), patch.object(step_forward.extractor, "extract", extract):
            res = client.post("/api/review/submissions/a1/judge", json={"decision": "accept_ai", "final_score": None})
        assert res.status_code == 201
        extract.assert_awaited_once()
        assert db.rows("step_attempts")[0]["structured_data"]["forward"]["ai_fields"] == {"優勢": "老師複核後抽出"}

    def test_student_reads_forwarded_fields(self, monkeypatch):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="u1", role="student")
        monkeypatch.setattr(practice, "check_step_access", lambda db, user, step: None)
        db = InMemoryDB(
            module_steps=[{"id": "s3", "module_id": "m1", "step_order": 3, "ai_tools": {"status": "published"}}],
            step_attempts=[{**row("s1", 1, 1, ai={"優勢": "喜歡積木"}), "user_id": "u1", "module_id": "m1"}],
        )
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(practice, "get_supabase", return_value=db):
            data = client.get("/api/practice/steps/s3/forwarded").json()["data"]
        assert data[0]["step_title"] == "第1關" and data[0]["fields"][0]["value"] == "喜歡積木"


def test_module_step_stores_forward_fields(monkeypatch):
    from tests.test_modules_api import MOD, PROFESSOR, TOOL_A, call, make_db
    main.app.dependency_overrides[get_current_user] = lambda: PROFESSOR
    try:
        db = make_db()
        res = call("POST", f"/{MOD}/steps", db, json={"tool_id": TOOL_A, "step_title": "現況", "pass_forward_keys": FIELDS})
        assert res.status_code == 201
        assert res.json()["data"]["steps"][-1]["pass_forward_keys"] == [
            {"label": "優勢", "description": "孩子能做到的事"}, {"label": "需求", "description": ""}]
        too_long = call("POST", f"/{MOD}/steps", db, json={"tool_id": TOOL_A, "step_title": "x",
                                                        "pass_forward_keys": [{"label": "字" * 31}]})
        assert too_long.status_code == 422
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
