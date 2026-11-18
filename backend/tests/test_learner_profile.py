"""
單元測試：學習者側寫（core/learner_profile.py）與它接上的地方

重點：
  - 熟練度：得分率的指數加權，越新越重；評分失敗的保底分不算
  - 錯誤：累計、跨步驟、只在會檢查它的步驟算缺席、連續 3 次沒出現 → 已改善，再出現就重算
  - 是否通過：已發布的教師判定優先於 AI
  - 每次更新新增一個版本、不覆蓋；失敗不往外拋
  - 記憶段落帶入跨步驟錯誤與弱構面；評分寫入記憶快照；沒跑 migration 015 也能評分
  - 教師端概述 API 的權限與內容
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from api import admin
from core.ai.memory import BotMemory, load_bot_memory
from core.auth import CurrentUser, get_current_user
from core.learner_profile import (
    FORGET_AFTER,
    build_profile,
    load_latest_profile,
    profile_prompt_lines,
    refresh_learner_profile,
)
from tests.inmemory_db import InMemoryDB

S1, S2, S3 = "step-1", "step-2", "step-3"
STEPS = {
    S1: {"step_order": 1, "step_title": "案例整理", "pass_score": 70},
    S2: {"step_order": 2, "step_title": "功能性現況", "pass_score": 70},
    S3: {"step_order": 3, "step_title": "長期目標", "pass_score": 70},
}


def att(step, n, *, at, dims=None, errors=(), score=None, status="revision_required", review=None, evaluated=True):
    return {
        "id": f"a-{step}-{n}", "step_id": step, "attempt_number": n, "status": status, "created_at": at,
        "module_steps": STEPS[step],
        "ai_evaluations": [{
            "total_score": score,
            "dimension_scores": [{"dimension": k, "score": v, "max_score": 4} for k, v in (dims or {}).items()],
            "detected_errors": [{"code": c, "label": f"錯誤{c}"} for c in errors],
        }] if evaluated else [],
        "teacher_reviews": [review] if review else [],
    }


# ──────────────────────────────────────────
# build_profile
# ──────────────────────────────────────────

class TestMastery:

    def test_exponential_weighting_favours_recent_attempts(self):
        p = build_profile([
            att(S1, 1, at="2026-11-01", dims={"去標籤化": 1}),   # 25%
            att(S1, 2, at="2026-11-02", dims={"去標籤化": 3}),   # 75%
        ])
        d = p["dimensions"][0]
        assert d["name"] == "去標籤化"
        assert d["mastery"] == pytest.approx(0.5)  # 0.5×0.75 + 0.5×0.25
        assert d["latest"] == 0.75 and d["trend"] == "up"

    def test_dimensions_sorted_weakest_first_and_merged_across_steps(self):
        p = build_profile([
            att(S1, 1, at="2026-11-01", dims={"客觀描述": 4, "去標籤化": 1}),
            att(S2, 1, at="2026-11-02", dims={"去標籤化": 1}),
        ])
        assert [d["name"] for d in p["dimensions"]] == ["去標籤化", "客觀描述"]
        assert p["dimensions"][0]["step_orders"] == [1, 2]

    def test_failed_dimension_is_not_counted(self):
        a = att(S1, 1, at="2026-11-01", dims={"去標籤化": 1})
        a["ai_evaluations"][0]["dimension_scores"][0]["failed"] = True
        assert build_profile([a])["dimensions"] == []

    def test_hint_only_attempts_do_not_count_as_evaluated(self):
        p = build_profile([att(S1, 1, at="2026-11-01", evaluated=False)])
        assert p["attempt_count"] == 1 and p["evaluated_count"] == 0
        assert p["steps"][0]["attempts"] == 1 and p["steps"][0]["evaluated"] == 0


class TestErrors:

    def test_counts_and_steps(self):
        p = build_profile([
            att(S1, 1, at="2026-11-01", errors=["E1"]),
            att(S2, 1, at="2026-11-02", errors=["E1", "E2"]),
        ])
        e1 = next(e for e in p["errors"] if e["code"] == "E1")
        assert e1["count"] == 2 and e1["step_orders"] == [1, 2] and e1["active"]
        assert p["errors"][0]["code"] == "E1"  # 次數多的排前面

    def test_absent_three_times_in_checking_step_means_improved(self):
        attempts = [att(S1, 1, at="2026-11-01", errors=["E1"])]
        attempts += [att(S1, n, at=f"2026-11-0{n}") for n in range(2, 2 + FORGET_AFTER)]
        e1 = build_profile(attempts)["errors"][0]
        assert e1["active"] is False
        assert e1["improved_at"] == f"2026-11-0{1 + FORGET_AFTER}"

    def test_attempts_in_other_steps_do_not_count_as_absence(self):
        """步驟 2 的錯誤，在不檢查它的步驟 3 沒出現，不能算改善"""
        attempts = [att(S2, 1, at="2026-11-01", errors=["E1"])]
        attempts += [att(S3, n, at=f"2026-11-0{n + 1}") for n in range(1, 5)]
        assert build_profile(attempts)["errors"][0]["active"] is True

    def test_reappearing_error_becomes_active_again(self):
        attempts = [att(S1, 1, at="2026-11-01", errors=["E1"])]
        attempts += [att(S1, n, at=f"2026-11-0{n}") for n in range(2, 5)]
        attempts.append(att(S1, 5, at="2026-11-05", errors=["E1"]))
        e1 = build_profile(attempts)["errors"][0]
        assert e1["active"] is True and e1["improved_at"] is None and e1["count"] == 2


class TestStepsAndTeacher:

    def test_published_teacher_score_overrides_ai(self):
        review = {"final_score": 80, "is_published": True, "final_feedback": "現況寫得很具體", "reviewed_at": "2026-11-03"}
        p = build_profile([att(S1, 1, at="2026-11-01", score=60, review=review)])
        assert p["steps"][0]["passed"] is True
        assert p["teacher_notes"] == [{"step_order": 1, "final_score": 80, "feedback": "現況寫得很具體", "reviewed_at": "2026-11-03"}]

    def test_unpublished_review_is_not_a_note(self):
        review = {"final_score": 80, "is_published": False, "final_feedback": "草稿"}
        p = build_profile([att(S1, 1, at="2026-11-01", score=60, review=review)])
        assert p["teacher_notes"] == [] and p["steps"][0]["passed"] is False

    def test_profile_contains_no_student_text(self):
        a = att(S1, 1, at="2026-11-01", dims={"去標籤化": 1})
        a["user_input_content"] = "小明的家長電話是……"
        assert "小明" not in str(build_profile([a]))


# ──────────────────────────────────────────
# 給 AI 助教的重點
# ──────────────────────────────────────────

class TestPromptLines:

    def test_cross_step_error_is_carried_into_later_step(self):
        p = build_profile([att(S2, 1, at="2026-11-01", errors=["E1"])])
        lines = profile_prompt_lines(p, current_step_order=5)
        assert any("錯誤E1" in line and "步驟 2" in line for line in lines)

    def test_single_occurrence_in_current_step_is_left_to_step_memory(self):
        p = build_profile([att(S1, 1, at="2026-11-01", errors=["E1"])])
        assert not any("常犯" in line for line in profile_prompt_lines(p, current_step_order=1))

    def test_weak_dimension_and_improved_errors(self):
        attempts = [att(S1, 1, at="2026-11-01", dims={"去標籤化": 1}, errors=["E1"])]
        attempts += [att(S1, n, at=f"2026-11-0{n}", dims={"去標籤化": 1}) for n in range(2, 5)]
        lines = profile_prompt_lines(build_profile(attempts), current_step_order=1)
        assert any(line.startswith("本模組較弱的構面：去標籤化") for line in lines)
        assert any(line.startswith("已改善") and "錯誤E1" in line for line in lines)

    def test_none_profile(self):
        assert profile_prompt_lines(None) == []


# ──────────────────────────────────────────
# 讀寫資料庫
# ──────────────────────────────────────────

def make_db(attempts=None, profiles=None):
    rows = [{**a, "user_id": "u1", "module_id": "m1"} for a in (attempts or [])]
    return InMemoryDB(step_attempts=rows, learner_profiles=profiles or [])


class TestRefresh:

    def test_each_refresh_adds_a_new_version(self):
        db = make_db([att(S1, 1, at="2026-11-01", dims={"去標籤化": 2})])
        refresh_learner_profile(db, "u1", "m1", "a-step-1-1")
        refresh_learner_profile(db, "u1", "m1", "a-step-1-1")
        rows = db.rows("learner_profiles")
        assert [r["version"] for r in rows] == [1, 2]
        assert rows[0]["source_attempt_id"] == "a-step-1-1"
        assert load_latest_profile(db, "u1", "m1")["version"] == 2

    def test_failure_is_swallowed(self):
        db = make_db([att(S1, 1, at="2026-11-01")])
        db.errors[("learner_profiles", "insert")] = RuntimeError('relation "learner_profiles" does not exist')
        assert refresh_learner_profile(db, "u1", "m1") is None

    def test_no_module_is_a_noop(self):
        db = make_db()
        assert refresh_learner_profile(db, "u1", None) is None
        assert db.calls == []


class TestMemoryIntegration:

    STEP_ROW = {"id": S3, "module_id": "m1", "step_order": 3, "ai_tools": {"teaching_strategy": {}}}

    def test_memory_includes_profile_section(self):
        profile = build_profile([att(S2, 1, at="2026-11-01", errors=["E1"])])
        db = make_db(profiles=[{"user_id": "u1", "module_id": "m1", "version": 4, "profile": profile}])
        memory = load_bot_memory(db, "u1", self.STEP_ROW)

        assert memory.profile_version == 4
        text = memory.to_prompt_text()
        assert "【這位學生在本模組的整體狀況】" in text and "錯誤E1" in text
        assert memory.to_event_data()["profile_highlights"] == memory.profile_lines

    def test_memory_works_without_profiles_table(self):
        db = make_db()
        db.errors[("learner_profiles", "select")] = RuntimeError('relation "learner_profiles" does not exist')
        memory = load_bot_memory(db, "u1", self.STEP_ROW)
        assert memory.profile_lines == [] and memory.profile_version is None

    def test_disabled_memory_skips_profile(self):
        row = {**self.STEP_ROW, "ai_tools": {"teaching_strategy": {"memory": {"enabled": False}}}}
        db = make_db(profiles=[{"user_id": "u1", "module_id": "m1", "version": 1,
                                "profile": build_profile([att(S2, 1, at="2026-11-01", errors=["E1"])])}])
        assert load_bot_memory(db, "u1", row).is_empty


# ──────────────────────────────────────────
# 評分流程：記憶快照與側寫更新
# ──────────────────────────────────────────

class TestSubmitHooks:

    def test_snapshot_insert_falls_back_before_migration_015(self):
        from api.practice import _insert_evaluation

        class DB:
            def __init__(self):
                self.rows = []

            def table(self, name):
                outer = self

                class Q:
                    def insert(self, row):
                        self.row = row
                        return self

                    def execute(self):
                        if "memory_snapshot" in self.row:
                            raise RuntimeError("Could not find the 'memory_snapshot' column of 'ai_evaluations'")
                        outer.rows.append(self.row)
                return Q()

        db = DB()
        _insert_evaluation(db, {"attempt_id": "a1", "total_score": 80, "memory_snapshot": {"text": ""}})
        assert db.rows == [{"attempt_id": "a1", "total_score": 80}]

    def test_other_insert_errors_still_raise(self):
        from api.practice import _insert_evaluation

        class DB:
            def table(self, name):
                class Q:
                    def insert(self, row):
                        return self

                    def execute(self):
                        raise RuntimeError("connection reset")
                return Q()

        with pytest.raises(RuntimeError):
            _insert_evaluation(DB(), {"attempt_id": "a1", "memory_snapshot": None})

    def test_snapshot_shape(self):
        from api.practice import _memory_snapshot

        memory = BotMemory(profile_lines=["本模組較弱的構面：去標籤化（約 25%）"], profile_version=2)
        snap = _memory_snapshot(memory)
        assert "去標籤化" in snap["text"] and snap["summary"]["profile_version"] == 2
        assert _memory_snapshot(None) is None


class TestSubmitEndToEnd:
    """沿用 test_practice_api 的假資料庫，確認評分後寫入快照並更新側寫"""

    def test_evaluation_writes_snapshot_and_refreshes_profile(self, monkeypatch):
        from api import practice
        from core.access import StepAccess
        from tests import test_practice_api as t

        monkeypatch.setattr(practice, "check_step_access", lambda db, user, step: StepAccess(mode="course"))
        monkeypatch.setattr(practice, "consume_quota", lambda db, access: None)
        monkeypatch.setattr(practice, "check_step_unlocked", lambda db, user, step: None)
        refreshed = []
        monkeypatch.setattr(practice, "refresh_learner_profile", lambda db, uid, mid, aid: refreshed.append((uid, mid, aid)))
        main.app.dependency_overrides[get_current_user] = lambda: t.STUDENT
        try:
            fake_db = t.FakeSupabase(t.STEP_ROW, prev_attempts=[])
            t.submit(fake_db, t.FULL_EVALUATION)
        finally:
            main.app.dependency_overrides.pop(get_current_user, None)

        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert "memory_snapshot" in payload and "text" in payload["memory_snapshot"]
        assert refreshed == [(t.STUDENT.id, t.BODY["module_id"], "attempt-new")]


# ──────────────────────────────────────────
# GET /api/admin/students/{user_id}/profile
# ──────────────────────────────────────────

UID = "aaaaaaaa-0000-4000-8000-000000000001"
MID = "b0000000-0000-4000-8000-000000000001"


def admin_db():
    rows = [{**att(S2, 1, at="2026-11-01", dims={"去標籤化": 1}, errors=["E1"]), "user_id": UID, "module_id": MID}]
    return InMemoryDB(
        profiles=[{"id": UID, "full_name": "王小美", "email": "amy@x.tw"}],
        learning_modules=[{"id": MID, "title": "學前 IEP"}],
        step_attempts=rows,
        learner_profiles=[{"user_id": UID, "module_id": MID, "version": 3, "profile": {}}],
    )


def get_profile(db, uid=UID, mid=MID):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(admin, "get_supabase", return_value=db):
        return client.get(f"/api/admin/students/{uid}/profile?module_id={mid}")


class TestProfileEndpoint:

    @pytest.fixture(autouse=True)
    def as_professor(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="prof-1", role="owner")
        yield
        main.app.dependency_overrides.pop(get_current_user, None)

    def test_returns_live_profile_and_saved_version(self):
        res = get_profile(admin_db())
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["student"]["full_name"] == "王小美"
        assert data["profile"]["errors"][0]["code"] == "E1"
        assert data["saved_version"] == 3

    def test_student_is_forbidden(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=UID, role="student")
        assert get_profile(admin_db()).status_code == 403

    def test_unknown_student_and_module(self):
        assert get_profile(admin_db(), uid="aaaaaaaa-0000-4000-8000-0000000000ff").json()["detail"]["code"] == "STUDENT_NOT_FOUND"
        assert get_profile(admin_db(), mid="b0000000-0000-4000-8000-0000000000ff").json()["detail"]["code"] == "MODULE_NOT_FOUND"

    def test_works_before_migration_015(self):
        db = admin_db()
        db.errors[("learner_profiles", "select")] = RuntimeError('relation "learner_profiles" does not exist')
        res = get_profile(db)
        assert res.status_code == 200 and res.json()["data"]["saved_version"] is None
