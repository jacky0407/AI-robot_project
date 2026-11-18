"""
單元測試：教授監控總覽 API (api/admin.py)

用記憶體版資料庫（tests/inmemory_db.py）；巢狀關聯（profiles、ai_evaluations…）直接寫在列資料裡。

重點：
  - 權限：限教師端（owner / assistant），學生 403
  - 學生 × 步驟每一格的狀態：not_started / in_progress / passed / needs_review
  - 教師判定優先於 AI（AI 沒過、老師改成過 → passed）
  - 只列課程中 active 的學生；停用的步驟不顯示；沒指派的模組 404
  - 還沒跑 migration 003／010 時退回舊欄位，總覽照常
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from api import admin
from api.admin import review_reasons, summarize_cell, summarize_step
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

PROFESSOR = CurrentUser(id="prof-1", role="owner")
ASSISTANT = CurrentUser(id="assistant-1", role="assistant")
STUDENT = CurrentUser(id="11111111-1111-4111-8111-111111111111", role="student")

COURSE = "c0000000-0000-4000-8000-000000000001"
OTHER_COURSE = "c0000000-0000-4000-8000-000000000002"
MOD = "b0000000-0000-4000-8000-000000000001"
UNASSIGNED_MOD = "b0000000-0000-4000-8000-000000000009"
S1 = "50000000-0000-4000-8000-000000000001"
S2 = "50000000-0000-4000-8000-000000000002"
S_OFF = "50000000-0000-4000-8000-0000000000ff"
AMY = "aaaaaaaa-0000-4000-8000-000000000001"
BEN = "bbbbbbbb-0000-4000-8000-000000000002"
CAT = "cccccccc-0000-4000-8000-000000000003"  # 申請中（pending），不列入

STEP_1 = {"id": S1, "module_id": MOD, "step_order": 1, "step_title": "步驟1 案例整理", "is_required": True,
          "is_active": True, "pass_score": 70, "require_teacher_review": False, "tool_id": "t1",
          "ai_tools": {"title": "案例整理教練"}}
STEP_2 = {"id": S2, "module_id": MOD, "step_order": 2, "step_title": "步驟2 功能性現況", "is_required": True,
          "is_active": True, "pass_score": 70, "require_teacher_review": True, "tool_id": "t2",
          "ai_tools": {"title": "現況教練"}}
STEP_OFF = {"id": S_OFF, "module_id": MOD, "step_order": 3, "step_title": "停用的步驟", "is_required": True,
            "is_active": False, "pass_score": 70, "require_teacher_review": False, "tool_id": "t1",
            "ai_tools": {"title": "案例整理教練"}}


def attempt(uid, step_id, n, *, score=None, status="revision_required", tutor_action="evaluate",
            low_confidence=False, review=None, hints=0, at="2026-11-08T10:00:00Z"):
    return {
        "id": f"att-{uid[:4]}-{step_id[-2:]}-{n}", "user_id": uid, "module_id": MOD, "step_id": step_id,
        "attempt_number": n, "status": status, "tutor_action": tutor_action, "created_at": at,
        "user_input_content": f"第 {n} 次作答",
        "ai_evaluations": [] if score is None else [{
            "total_score": score, "needs_teacher_review": low_confidence, "dimension_scores": [],
            "feedback_text": "回饋", "confidence": 0.4 if low_confidence else 0.9, "detected_errors": [],
        }],
        "teacher_reviews": [review] if review else [],
        "prompt_logs": [{"id": f"h{i}", "hint_level": i + 1, "created_at": at} for i in range(hints)],
    }


def make_db(attempts=None, steps=None) -> InMemoryDB:
    return InMemoryDB(
        courses=[{"id": COURSE, "title": "部署測試課程", "is_archived": False},
                 {"id": OTHER_COURSE, "title": "沒有指派模組的課", "is_archived": False}],
        course_modules=[{"course_id": COURSE, "module_id": MOD,
                         "learning_modules": {"id": MOD, "title": "學前 IEP", "is_published": True}}],
        course_members=[
            {"course_id": COURSE, "user_id": AMY, "status": "active", "joined_at": "2026-11-01",
             "profiles": {"full_name": "王小美", "email": "amy@x.tw", "role": "student"}},
            {"course_id": COURSE, "user_id": BEN, "status": "active", "joined_at": "2026-11-02",
             "profiles": {"full_name": "李小斌", "email": "ben@x.tw", "role": "student"}},
            {"course_id": COURSE, "user_id": CAT, "status": "pending", "joined_at": "2026-11-03",
             "profiles": {"full_name": "陳小貓", "email": "cat@x.tw", "role": "student"}},
            {"course_id": COURSE, "user_id": "prof-1", "status": "active", "joined_at": "2026-11-01",
             "profiles": {"full_name": "吳教授", "email": "prof@x.tw", "role": "owner"}},
        ],
        module_steps=steps if steps is not None else [STEP_1, STEP_2, STEP_OFF],
        step_attempts=attempts or [],
        profiles=[{"id": AMY, "full_name": "王小美", "email": "amy@x.tw"}],
    )


@pytest.fixture(autouse=True)
def logged_in_professor():
    main.app.dependency_overrides[get_current_user] = lambda: PROFESSOR
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def login_as(user):
    main.app.dependency_overrides[get_current_user] = lambda: user


def get(path, db):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(admin, "get_supabase", return_value=db):
        return client.get(f"/api/admin{path}")


def overview(db, **params):
    query = "&".join(f"{k}={v}" for k, v in {"course_id": COURSE, **params}.items())
    return get(f"/overview?{query}", db)


# ──────────────────────────────────────────
# 權限
# ──────────────────────────────────────────

class TestPermissions:

    def test_student_is_forbidden(self):
        login_as(STUDENT)
        res = overview(make_db())
        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "PERMISSION_DENIED"

    def test_student_cannot_open_student_step_detail(self):
        login_as(STUDENT)
        res = get(f"/students/{AMY}/steps/{S1}", make_db())
        assert res.status_code == 403

    def test_assistant_can_read_overview(self):
        login_as(ASSISTANT)
        assert overview(make_db()).status_code == 200


# ──────────────────────────────────────────
# GET /overview
# ──────────────────────────────────────────

class TestOverview:

    def test_lists_only_active_students_and_active_steps(self):
        data = overview(make_db()).json()["data"]

        assert data["course"]["title"] == "部署測試課程"
        assert data["student_count"] == 2
        module = data["modules"][0]
        assert [s["step_id"] for s in module["steps"]] == [S1, S2]  # 停用的步驟不顯示
        names = {r["full_name"] for r in module["students"]}
        assert names == {"王小美", "李小斌"}  # pending 的學生與教授本人不列入

    def test_cell_statuses(self):
        attempts = [
            # 小美：步驟1 第一次 50 分、第二次 82 分通過；步驟2 低信心等老師
            attempt(AMY, S1, 1, score=50, hints=1, at="2026-11-08T09:00:00Z"),
            attempt(AMY, S1, 2, score=82, status="passed", at="2026-11-08T09:30:00Z"),
            attempt(AMY, S2, 1, score=65, low_confidence=True, at="2026-11-08T10:00:00Z"),
            # 小斌：步驟1 只拿到提示（沒評分）
            attempt(BEN, S1, 1, tutor_action="give_hint", hints=2, at="2026-11-07T10:00:00Z"),
        ]
        module = overview(make_db(attempts)).json()["data"]["modules"][0]
        rows = {r["user_id"]: r for r in module["students"]}

        amy1 = rows[AMY]["cells"][S1]
        assert amy1["status"] == "passed"
        assert (amy1["attempt_count"], amy1["evaluated_count"]) == (2, 2)
        assert (amy1["latest_ai_score"], amy1["best_ai_score"]) == (82, 82)
        assert amy1["hints_used"] == 1

        amy2 = rows[AMY]["cells"][S2]
        assert amy2["status"] == "needs_review"
        assert amy2["review_reasons"] == ["low_confidence", "step_requires_review"]

        ben1 = rows[BEN]["cells"][S1]
        assert ben1["status"] == "in_progress"
        assert ben1["latest_ai_score"] is None and ben1["hints_used"] == 2
        assert rows[BEN]["cells"][S2]["status"] == "not_started"

        assert rows[AMY]["required_passed"] == 1 and rows[AMY]["required_total"] == 2
        assert rows[AMY]["last_activity_at"] == "2026-11-08T10:00:00Z"

    def test_students_needing_review_come_first(self):
        attempts = [
            attempt(BEN, S1, 1, score=40, tutor_action="escalate", at="2026-11-01T10:00:00Z"),
            attempt(AMY, S1, 1, score=90, status="passed", at="2026-11-08T10:00:00Z"),
        ]
        module = overview(make_db(attempts)).json()["data"]["modules"][0]
        assert [r["user_id"] for r in module["students"]] == [BEN, AMY]

    def test_step_summary(self):
        attempts = [
            attempt(AMY, S1, 1, score=90, status="passed"),
            attempt(BEN, S1, 1, score=60),
        ]
        module = overview(make_db(attempts)).json()["data"]["modules"][0]
        summary = module["steps"][0]["summary"]
        assert summary == {"started": 2, "passed": 1, "needs_review": 0, "avg_score": 75.0, "avg_latest_ai_score": 75.0}
        assert module["steps"][1]["summary"]["avg_latest_ai_score"] is None

    def test_module_filter_must_be_assigned_to_course(self):
        res = overview(make_db(), module_id=UNASSIGNED_MOD)
        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "MODULE_NOT_IN_COURSE"

    def test_module_filter_returns_that_module(self):
        res = overview(make_db(), module_id=MOD)
        assert [m["module_id"] for m in res.json()["data"]["modules"]] == [MOD]

    def test_unknown_course_returns_404(self):
        res = overview(make_db(), course_id="c0000000-0000-4000-8000-0000000000aa")
        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "COURSE_NOT_FOUND"

    def test_invalid_course_id_returns_422_without_touching_database(self):
        db = make_db()
        res = get("/overview?course_id=not-a-uuid", db)
        assert res.status_code == 422
        assert db.calls == []

    def test_course_without_modules_returns_empty_list(self):
        res = overview(make_db(), course_id=OTHER_COURSE)
        assert res.status_code == 200
        assert res.json()["data"]["modules"] == []

    def test_works_before_migration_010(self):
        """沒有 is_active 欄位時退回舊查詢，步驟視為全部啟用"""
        db = make_db(steps=[{k: v for k, v in STEP_1.items() if k != "is_active"}])
        original = db.table

        def table(name):
            q = original(name)
            if name == "module_steps":
                select = q.select

                def guarded(cols="*", **kw):
                    if "is_active" in cols:
                        raise RuntimeError('column module_steps.is_active does not exist')
                    return select(cols, **kw)
                q.select = guarded
            return q

        db.table = table
        res = overview(db)
        assert res.status_code == 200
        assert [s["step_id"] for s in res.json()["data"]["modules"][0]["steps"]] == [S1]

    def test_does_not_write_anything(self):
        db = make_db([attempt(AMY, S1, 1, score=50)])
        overview(db)
        assert {op for _, op, _ in db.calls} == {"select"}


# ──────────────────────────────────────────
# GET /students/{user_id}/steps/{step_id}
# ──────────────────────────────────────────

class TestStudentStep:

    def test_returns_attempts_newest_first_with_evaluation_and_review(self):
        review = {"decision": "modify", "final_score": 75, "final_feedback": "好", "is_published": True,
                  "reviewed_at": "2026-11-08T12:00:00Z"}
        attempts = [
            attempt(AMY, S1, 1, score=50, hints=2),
            attempt(AMY, S1, 2, score=65, review=review),
            attempt(BEN, S1, 1, score=99),  # 別的學生，不能混進來
        ]
        res = get(f"/students/{AMY}/steps/{S1}", make_db(attempts))
        assert res.status_code == 200
        data = res.json()["data"]

        assert data["student"]["full_name"] == "王小美"
        assert data["step"]["step_title"] == "步驟1 案例整理"
        assert [a["attempt_number"] for a in data["attempts"]] == [2, 1]
        latest = data["attempts"][0]
        assert latest["ai_evaluation"]["total_score"] == 65
        assert latest["teacher_review"]["final_score"] == 75
        assert data["attempts"][1]["hints"][0]["hint_level"] == 1
        # 教師把 65 分改成 75 分（門檻 70）→ 這一步算通過
        assert data["summary"]["passed"] is True
        assert data["summary"]["teacher_score"] == 75

    def test_unknown_step_returns_404(self):
        res = get(f"/students/{AMY}/steps/50000000-0000-4000-8000-0000000000aa", make_db())
        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "STEP_NOT_FOUND"

    def test_unknown_student_returns_404(self):
        res = get(f"/students/{BEN}/steps/{S1}", make_db())
        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "STUDENT_NOT_FOUND"


# ──────────────────────────────────────────
# 純函式
# ──────────────────────────────────────────

class TestSummarizeCell:

    def test_teacher_pass_overrides_ai_fail(self):
        review = {"final_score": 80, "is_published": True}
        cell = summarize_cell([attempt(AMY, S1, 1, score=55, review=review)], STEP_1)
        assert cell["passed"] is True and cell["status"] == "passed"

    def test_teacher_fail_overrides_ai_pass(self):
        review = {"final_score": 50, "is_published": True}
        cell = summarize_cell([attempt(AMY, S1, 1, score=90, status="passed", review=review)], STEP_1)
        assert cell["passed"] is False and cell["status"] == "in_progress"

    def test_unpublished_review_still_counts_as_reviewed(self):
        """已判定但未發布：不再等老師，但分數不採用"""
        review = {"final_score": 90, "is_published": False}
        cell = summarize_cell([attempt(AMY, S2, 1, score=60, review=review)], STEP_2)
        assert cell["needs_review_count"] == 0
        assert cell["teacher_score"] is None and cell["passed"] is False

    def test_hint_only_attempt_does_not_need_review(self):
        a = attempt(AMY, S2, 1, tutor_action="give_hint")
        assert review_reasons(a, STEP_2) == []

    def test_escalated_attempt_needs_review_without_evaluation(self):
        a = attempt(AMY, S1, 1, tutor_action="escalate")
        assert review_reasons(a, STEP_1) == ["escalated"]

    def test_passed_and_needs_review_are_separate(self):
        """排練回饋：老師已評 80 分通過、之後又有作答等老師 → 仍是「已通過」，另外標待複核"""
        review = {"final_score": 80, "is_published": True}
        cell = summarize_cell([
            attempt(AMY, S2, 1, score=25, review=review, at="2026-11-01"),
            attempt(AMY, S2, 2, score=30, tutor_action="escalate", at="2026-11-02"),
        ], STEP_2)
        assert cell["progress"] == "passed" and cell["needs_review_count"] == 1
        assert (cell["score"], cell["score_source"]) == (80, "teacher")
        assert cell["status"] == "needs_review"  # 舊欄位不變，給舊前端

    def test_score_falls_back_to_ai(self):
        cell = summarize_cell([attempt(AMY, S1, 1, score=25)], STEP_1)
        assert (cell["progress"], cell["score"], cell["score_source"]) == ("not_passed", 25, "ai")
        assert summarize_cell([attempt(AMY, S1, 1, tutor_action="give_hint")], STEP_1)["score_source"] is None

    def test_step_average_prefers_teacher_score(self):
        review = {"final_score": 80, "is_published": True}
        cells = [summarize_cell([attempt(AMY, S1, 1, score=20, review=review)], STEP_1),
                 summarize_cell([attempt(BEN, S1, 1, score=40)], STEP_1)]
        summary = summarize_step(STEP_1, cells)
        assert summary["avg_score"] == 60.0 and summary["avg_latest_ai_score"] == 30.0

    def test_empty_cell(self):
        assert summarize_cell([], STEP_1)["status"] == "not_started"

    def test_step_summary_ignores_unscored(self):
        cells = [summarize_cell([], STEP_1), summarize_cell([attempt(AMY, S1, 1, score=70)], STEP_1)]
        assert summarize_step(STEP_1, cells)["avg_latest_ai_score"] == 70.0
