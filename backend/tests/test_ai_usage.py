"""
#81 AI 用量與費用、每月預算（core/ai_usage.py、api/usage.py）

全部 Mock：LLM 換成假的、資料庫用記憶體版或假的 Supabase。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import main
from api import chat, practice, usage as usage_api
from core import ai_usage
from core.ai.llm_utils import invoke_with_retry
from core.ai_usage import UsageContext, budget_status, check_budget, estimate_cost, month_start_utc, record, track_usage
from core.auth import CurrentUser, get_current_user
from core.config import get_settings
from tests.inmemory_db import InMemoryDB

COURSE = "c0000000-0000-4000-8000-000000000001"


def ctx(db, **kw):
    return UsageContext(db=db, user_id="u1", course_id=COURSE, module_id="m1", step_id="s1",
                        tool_id="t1", access_mode="course", **kw)


# ──────────────────────────────────────────
# 費用估算與記錄
# ──────────────────────────────────────────

class TestRecord:

    def test_cost_uses_price_table(self):
        # gemini-3.5-flash：輸入 1.5、輸出 9 美元／百萬 token
        assert estimate_cost("gemini-3.5-flash", 1_000_000, 100_000) == pytest.approx(1.5 + 0.9)
        assert estimate_cost("models/gemini-3.5-flash-lite", 1_000_000, 0) == pytest.approx(0.30)

    def test_unknown_model_costs_zero(self):
        assert estimate_cost("some-new-model", 1000, 1000) == 0

    def test_nothing_is_recorded_without_context(self):
        db = InMemoryDB(ai_usage_logs=[])
        record("synthesize", "gemini-3.5-flash", input_tokens=10)
        assert db.rows("ai_usage_logs") == []

    def test_row_has_who_what_and_cost(self):
        db = InMemoryDB(ai_usage_logs=[])
        with track_usage(ctx(db, key_source="student")):
            record("chat_bot:家長", "gemini-3.5-flash", input_tokens=2000, output_tokens=500, latency_ms=800)
        row = db.rows("ai_usage_logs")[0]
        assert row["feature"] == "chat_bot"              # 冒號後面的機器人名稱不存
        assert row["course_id"] == COURSE and row["key_source"] == "student"
        assert row["cost_usd"] == pytest.approx((2000 * 1.5 + 500 * 9) / 1_000_000)

    def test_record_failure_never_breaks_scoring(self):
        db = InMemoryDB()
        db.errors[("ai_usage_logs", "insert")] = RuntimeError("table missing")
        with track_usage(ctx(db)):
            record("synthesize", "gemini-3.5-flash")   # 不拋例外

    @pytest.mark.asyncio
    async def test_llm_wrapper_records_tokens(self):
        db = InMemoryDB(ai_usage_logs=[])
        llm = AsyncMock()
        llm.model = "models/gemini-3.5-flash"
        llm.ainvoke = AsyncMock(return_value=AIMessage(
            content="好", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}))
        with track_usage(ctx(db)):
            assert await invoke_with_retry(llm, [], label="synthesize") == "好"
        row = db.rows("ai_usage_logs")[0]
        assert (row["model"], row["input_tokens"], row["output_tokens"], row["success"]) == ("gemini-3.5-flash", 120, 30, True)

    @pytest.mark.asyncio
    async def test_llm_wrapper_records_final_failure(self):
        db = InMemoryDB(ai_usage_logs=[])
        llm = AsyncMock()
        llm.model = "gemini-3.5-flash"
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("429"))
        with track_usage(ctx(db)), pytest.raises(RuntimeError):
            await invoke_with_retry(llm, [], label="synthesize", max_attempts=2, backoff=0)
        rows = db.rows("ai_usage_logs")
        assert len(rows) == 1 and rows[0]["success"] is False and "429" in rows[0]["error"]


# ──────────────────────────────────────────
# 每月預算
# ──────────────────────────────────────────

def budget_db(budget, used):
    db = InMemoryDB(courses=[{"id": COURSE, "monthly_ai_budget_usd": budget}])
    db.rpc_handlers["ai_usage_total"] = lambda db, **p: used
    return db


class TestBudget:

    def test_month_starts_at_taipei_midnight(self):
        # 台灣 12/1 00:30 = UTC 11/30 16:30 → 這個月從 UTC 11/30 16:00 開始
        start = month_start_utc(datetime(2026, 11, 30, 16, 30, tzinfo=timezone.utc))
        assert start == datetime(2026, 11, 30, 16, 0, tzinfo=timezone.utc)

    def test_no_budget_means_no_limit(self):
        assert budget_status(budget_db(None, 999), course_id=COURSE, access_mode="course") is None

    @pytest.mark.parametrize("used, level", [(5, "ok"), (8, "warn"), (9.9, "warn"), (10, "exceeded"), (20, "exceeded")])
    def test_levels(self, used, level):
        st = budget_status(budget_db(10, used), course_id=COURSE, access_mode="course")
        assert st["level"] == level and st["budget_usd"] == 10

    def test_exceeded_blocks_new_work(self):
        with pytest.raises(HTTPException) as e:
            check_budget(budget_db(10, 10), course_id=COURSE, access_mode="course")
        assert e.value.status_code == 429 and e.value.detail["code"] == "BUDGET_EXCEEDED"

    def test_student_own_key_and_staff_are_never_limited(self):
        for mode in ("self_key", "staff"):
            assert budget_status(budget_db(1, 999), course_id=COURSE, access_mode=mode) is None

    def test_autonomous_budget_comes_from_settings(self, monkeypatch):
        monkeypatch.setenv("AUTONOMOUS_MONTHLY_BUDGET_USD", "5")
        get_settings.cache_clear()
        try:
            db = InMemoryDB()
            db.rpc_handlers["ai_usage_total"] = lambda db, **p: 6 if p["p_autonomous"] else 0
            st = budget_status(db, course_id=None, access_mode="permission")
        finally:
            get_settings.cache_clear()
        assert st["scope"] == "autonomous" and st["level"] == "exceeded"

    def test_budget_lookup_failure_does_not_block_students(self):
        db = InMemoryDB(courses=[{"id": COURSE, "monthly_ai_budget_usd": 10}])
        db.errors[("rpc", "ai_usage_total")] = RuntimeError("function missing")
        assert budget_status(db, course_id=COURSE, access_mode="course") is None


# ──────────────────────────────────────────
# 送出作答與聊天室
# ──────────────────────────────────────────

class TestEndpoints:

    def test_practice_records_usage_and_blocks_when_exceeded(self, monkeypatch):
        from tests import test_practice_api as t
        main.app.dependency_overrides[get_current_user] = lambda: t.STUDENT
        monkeypatch.setattr(practice, "check_step_access", lambda db, user, step: t.StepAccess(mode="course"))
        monkeypatch.setattr(practice, "check_step_unlocked", lambda db, user, step: None)
        charged = []
        monkeypatch.setattr(practice, "consume_quota", lambda db, access: charged.append(1))
        try:
            # 預算還夠：評分照常，每次 AI 呼叫都記一筆
            fake = t.FakeSupabase(t.STEP_ROW, prev_attempts=[])
            responses = [t.dim_response("客觀事實辨識", 3), t.dim_response("推論與假設區分", 3),
                         t.dim_response("缺漏資訊提問", 3), t.SYNTH_RESPONSE]
            res, _ = t.submit(fake, responses)
            assert res.status_code == 200
            features = [c["payload"]["feature"] for c in fake.find("ai_usage_logs", "insert")]
            assert features.count("score_dimension") == 3 and "synthesize" in features

            # 預算用完：429，不建立作答、不扣次數
            def exceeded(db, *, course_id, access_mode):
                raise HTTPException(status_code=429, detail={"code": "BUDGET_EXCEEDED", "message": "x"})
            monkeypatch.setattr(practice, "check_budget", exceeded)
            fake = t.FakeSupabase(t.STEP_ROW, prev_attempts=[])
            charged.clear()
            res, _ = t.submit(fake, [])
            assert res.status_code == 429 and res.json()["detail"]["code"] == "BUDGET_EXCEEDED"
            assert fake.find("step_attempts", "insert") == [] and charged == []
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

    def test_chat_evaluation_is_allowed_after_budget_runs_out(self, monkeypatch):
        """已經聊完的學生還能送出評分，完成這一關"""
        from tests import test_chat_api as c
        main.app.dependency_overrides[get_current_user] = lambda: c.STUDENT
        monkeypatch.setattr(chat, "check_step_access", lambda db, user, step: c.StepAccess(mode="course"))
        monkeypatch.setattr(chat, "check_step_unlocked", lambda db, user, step: None)
        monkeypatch.setattr(chat, "ensure_session", lambda db, sid, uid: sid)
        monkeypatch.setattr(chat, "consume_quota", lambda db, access: None)
        monkeypatch.setattr(chat, "refresh_learner_profile", lambda *a, **k: None)
        calls = []

        def exceeded(db, *, course_id, access_mode):
            calls.append(1)
            raise HTTPException(status_code=429, detail={"code": "BUDGET_EXCEEDED", "message": "x"})
        monkeypatch.setattr(chat, "check_budget", exceeded)
        try:
            res, _, _ = c.send(c.make_db(), "你好")
            assert res.status_code == 429                  # 新訊息被擋
            db = c.make_db(rubric=[], messages=list(c.HISTORY))
            with patch.object(practice.feedback_agent, "feedback", AsyncMock(return_value="好")):
                res, events, _ = c.send(db, evaluate=True)
            assert res.status_code == 200 and "pass_status" in dict(events)   # 送出評分照常
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)


# ──────────────────────────────────────────
# 老師看用量
# ──────────────────────────────────────────

class TestUsageApi:

    @pytest.fixture(autouse=True)
    def teacher(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="prof-1", role="owner")
        yield
        main.app.dependency_overrides.pop(get_current_user, None)

    def get(self, db, month=None):
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(usage_api, "get_supabase", return_value=db):
            return client.get(f"/api/courses/{COURSE}/ai-usage", params={"month": month} if month else None)

    def db(self):
        db = InMemoryDB(courses=[{"id": COURSE, "title": "課", "monthly_ai_budget_usd": 10}],
                        ai_tools=[{"id": "t1", "title": "評分老師"}])
        db.rpc_handlers["ai_usage_summary"] = lambda db, **p: [
            {"day": "2026-11-20", "feature": "score_dimension", "model": "gemini-3.5-flash", "tool_id": "t1",
             "key_source": "platform", "calls": 30, "failed": 1, "input_tokens": 90000, "output_tokens": 20000, "cost_usd": 0.315},
            {"day": "2026-11-21", "feature": "chat_bot", "model": "gemini-3.5-flash-lite", "tool_id": "t1",
             "key_source": "student", "calls": 10, "failed": 0, "input_tokens": 5000, "output_tokens": 2000, "cost_usd": 0.0065},
        ]
        db.rpc_handlers["ai_usage_total"] = lambda db, **p: 0.315
        return db

    def test_summary_groups_and_separates_student_keys(self):
        data = self.get(self.db(), "2026-11").json()["data"]
        assert data["total"]["calls"] == 40 and data["total"]["failed"] == 1
        assert data["total"]["platform_cost_usd"] == pytest.approx(0.315)
        assert data["total"]["student_key_cost_usd"] == pytest.approx(0.0065)
        assert {f["name"] for f in data["by_feature"]} == {"逐項評分", "聊天回覆"}
        assert data["by_tool"][0]["name"] == "評分老師"
        assert [d["day"] for d in data["by_day"]] == ["2026-11-20", "2026-11-21"]

    def test_bad_month(self):
        assert self.get(self.db(), "2026-13").status_code == 400

    def test_before_migration_023(self):
        db = self.db()
        db.errors[("rpc", "ai_usage_summary")] = RuntimeError("function ai_usage_summary does not exist")
        assert self.get(db).status_code == 503

    def test_students_cannot_see_usage(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="s1", role="student")
        assert self.get(self.db()).status_code == 403
