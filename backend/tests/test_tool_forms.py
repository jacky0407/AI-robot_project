"""
單元測試：工具專屬表單與質性反思（#80，core/tool_forms.py、api/forms.py、migration 029）

  - 題目檢查：題型、選項、量尺範圍、自動編號、重複編號
  - 填答檢查：必答、不適用、選項與量尺範圍、字數、個資偵測
  - 出現時點：練習前、結束後、看完回饋後、修正後、每 N 次、教師發布；填過（或略過）就不再出現
  - 教師端：只有建立者或教授能新增與修改；修改產生新版本、舊答案仍指向舊版
  - 學生端：必填不能略過、同一時點不重複填、填答連到作答與當時的 AI 分數、課程
  - 填了才算完成（gate = completion）：沒填答之前這一關不算通過
不連 Supabase（記憶體資料庫），不呼叫 AI。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from api import forms as forms_api
from core import step_unlock
from core.access import StepAccess
from core.auth import CurrentUser, get_current_user
from core.tool_forms import (
    FormError,
    StepState,
    blocks_completion,
    is_due,
    normalize_schema,
    slot_answered,
    steps_blocked_by_forms,
    validate_answers,
    validate_settings,
)
from tests.inmemory_db import InMemoryDB

PROF = CurrentUser(id="prof-1", role="owner")
CREATOR = CurrentUser(id="t-1", role="assistant")
OTHER_TEACHER = CurrentUser(id="t-2", role="assistant")
STUDENT = CurrentUser(id="stu-1", role="student")

QUESTIONS = [
    {"type": "scale", "label": "這次的信心", "min": 1, "max": 5, "required": True},
    {"type": "multi", "label": "用了哪些策略", "options": ["觀察", "記錄", "訪談"], "allow_na": True},
    {"type": "long", "label": "這次學到什麼"},
]


# ── 題目 ─────────────────────────────────────────────────────────────────────


class TestSchema:

    def test_questions_get_ids_when_missing(self):
        schema = normalize_schema({"questions": QUESTIONS})
        assert [q["id"] for q in schema["questions"]] == ["q1", "q2", "q3"]

    def test_auto_ids_skip_ids_already_given(self):
        schema = normalize_schema({"questions": [{"id": "q1", "type": "short", "label": "A"},
                                                 {"type": "short", "label": "B"}]})
        assert [q["id"] for q in schema["questions"]] == ["q1", "q2"]

    def test_duplicate_question_id_is_rejected(self):
        with pytest.raises(FormError, match="重複"):
            normalize_schema({"questions": [{"id": "a", "type": "short", "label": "A"},
                                            {"id": "a", "type": "short", "label": "B"}]})

    def test_empty_form_is_rejected(self):
        with pytest.raises(FormError, match="至少要有一題"):
            normalize_schema({"questions": []})

    def test_unknown_type_is_rejected(self):
        with pytest.raises(FormError, match="題型"):
            normalize_schema({"questions": [{"type": "matrix", "label": "A"}]})

    def test_choice_needs_two_distinct_options(self):
        with pytest.raises(FormError, match="兩個選項"):
            normalize_schema({"questions": [{"type": "single", "label": "A", "options": ["是"]}]})
        with pytest.raises(FormError, match="重複的選項"):
            normalize_schema({"questions": [{"type": "single", "label": "A", "options": ["是", "是"]}]})

    def test_scale_range_must_be_valid(self):
        with pytest.raises(FormError, match="量尺"):
            normalize_schema({"questions": [{"type": "scale", "label": "A", "min": 5, "max": 5}]})
        with pytest.raises(FormError, match="量尺"):
            normalize_schema({"questions": [{"type": "scale", "label": "A", "min": 1, "max": 11}]})

    def test_every_n_needs_n(self):
        with pytest.raises(FormError, match="N"):
            validate_settings("every_n_attempts", None, "none")
        validate_settings("every_n_attempts", 3, "completion")


# ── 填答 ─────────────────────────────────────────────────────────────────────


SCHEMA = normalize_schema({"questions": QUESTIONS})


class TestAnswers:

    def test_valid_answers_are_kept_in_option_order(self):
        saved = validate_answers(SCHEMA, {"answers": {"q1": 4, "q2": ["記錄", "觀察"], "q3": "  要先觀察  "}})
        assert saved == {"answers": {"q1": 4, "q2": ["觀察", "記錄"], "q3": "要先觀察"}, "na": []}

    def test_required_question_must_be_answered(self):
        with pytest.raises(FormError) as e:
            validate_answers(SCHEMA, {"answers": {"q3": "好"}})
        assert e.value.code == "ANSWER_REQUIRED" and e.value.extra["question_id"] == "q1"

    def test_not_applicable_only_where_allowed(self):
        saved = validate_answers(SCHEMA, {"answers": {"q1": 3}, "na": ["q2"]})
        assert saved["na"] == ["q2"] and "q2" not in saved["answers"]
        with pytest.raises(FormError, match="不適用"):
            validate_answers(SCHEMA, {"answers": {}, "na": ["q1"]})

    def test_scale_out_of_range_is_rejected(self):
        with pytest.raises(FormError, match="1～5"):
            validate_answers(SCHEMA, {"answers": {"q1": 6}})
        with pytest.raises(FormError, match="整數"):
            validate_answers(SCHEMA, {"answers": {"q1": True}})

    def test_unknown_option_is_rejected(self):
        with pytest.raises(FormError, match="選項"):
            validate_answers(SCHEMA, {"answers": {"q1": 3, "q2": ["猜的"]}})

    def test_text_too_long_is_rejected(self):
        with pytest.raises(FormError, match="5000"):
            validate_answers(SCHEMA, {"answers": {"q1": 3, "q3": "字" * 5001}})

    def test_text_with_personal_data_is_blocked(self):
        with pytest.raises(FormError) as e:
            validate_answers(SCHEMA, {"answers": {"q1": 3, "q3": "請打 0912-345-678 給家長"}})
        assert e.value.code == "PII_DETECTED"

    def test_questions_not_in_form_are_dropped(self):
        saved = validate_answers(SCHEMA, {"answers": {"q1": 2, "hack": "x"}})
        assert saved["answers"] == {"q1": 2}


# ── 出現時點 ─────────────────────────────────────────────────────────────────


def form(timing, **kw):
    return {"form_key": "k", "trigger_timing": timing, **kw}


AFTER_SECOND = StepState(latest_attempt_id="a2", latest_attempt_number=2, latest_has_feedback=True)


class TestTiming:

    def test_pre_practice_shows_until_answered(self):
        assert is_due(form("pre_practice"), StepState(), answered=False)
        assert not is_due(form("pre_practice"), StepState(), answered=True)

    def test_post_practice_waits_until_step_passed(self):
        assert not is_due(form("post_practice"), AFTER_SECOND, answered=False)
        assert is_due(form("post_practice"), StepState(step_passed=True), answered=False)

    def test_after_feedback_needs_feedback_on_latest_attempt(self):
        waiting = StepState(latest_attempt_id="a1", latest_attempt_number=1, latest_has_feedback=False)
        assert not is_due(form("after_feedback"), waiting, answered=False)
        assert is_due(form("after_feedback"), AFTER_SECOND, answered=False)

    def test_post_revision_only_from_second_attempt(self):
        first = StepState(latest_attempt_id="a1", latest_attempt_number=1, latest_has_feedback=True)
        assert not is_due(form("post_revision"), first, answered=False)
        assert is_due(form("post_revision"), AFTER_SECOND, answered=False)

    def test_every_n_attempts(self):
        f = form("every_n_attempts", trigger_every_n=2)
        assert is_due(f, AFTER_SECOND, answered=False)
        third = StepState(latest_attempt_id="a3", latest_attempt_number=3, latest_has_feedback=True)
        assert not is_due(f, third, answered=False)

    def test_teacher_release_waits_for_release(self):
        assert not is_due(form("teacher_release"), StepState(), answered=False)
        assert is_due(form("teacher_release", released_at="2026-11-26T00:00:00Z"), StepState(), answered=False)

    def test_attempt_forms_are_answered_per_attempt(self):
        old = [{"attempt_id": "a1", "skipped": False}]
        assert not slot_answered(form("after_feedback"), old, AFTER_SECOND)
        assert slot_answered(form("after_feedback"), old + [{"attempt_id": "a2"}], AFTER_SECOND)

    def test_step_forms_are_answered_once_per_step(self):
        assert slot_answered(form("pre_practice"), [{"attempt_id": None, "skipped": True}], AFTER_SECOND)

    def test_completion_gate_needs_a_real_answer_not_skip(self):
        gated = form("after_feedback", gate="completion")
        assert blocks_completion(gated, [{"skipped": True}])
        assert not blocks_completion(gated, [{"skipped": True}, {"skipped": False}])
        assert not blocks_completion(form("after_feedback", gate="none"), [])


# ── API ──────────────────────────────────────────────────────────────────────


TOOL = "tool-1"
STEP = "step-1"
MODULE = "mod-1"


def make_db(**extra):
    return InMemoryDB(
        ai_tools=[{"id": TOOL, "title": "觀察紀錄教練", "created_by": CREATOR.id, "status": "published"}],
        module_steps=[{"id": STEP, "module_id": MODULE, "tool_id": TOOL, "step_order": 1, "step_title": "觀察",
                       "is_required": True, "pass_score": 70,
                       "ai_tools": {"id": TOOL, "status": "published", "title": "觀察紀錄教練"}}],
        step_attempts=extra.pop("step_attempts", []),
        forms=extra.pop("forms", []),
        form_responses=extra.pop("form_responses", []),
        profiles=[{"id": STUDENT.id, "full_name": "王小美"}],
        audit_logs=[],
        **extra,
    )


@pytest.fixture
def client():
    yield TestClient(main.app, raise_server_exceptions=False)
    main.app.dependency_overrides.pop(get_current_user, None)


def call(client, db, user, method, path, **kw):
    main.app.dependency_overrides[get_current_user] = lambda: user
    with patch.object(forms_api, "get_supabase", return_value=db), \
         patch.object(forms_api, "check_step_access", return_value=StepAccess(mode="course")), \
         patch.object(forms_api, "course_for_access", return_value="course-1"):
        return client.request(method, path, **kw)


def new_form(client, db, user=CREATOR, **body):
    payload = {"title": "練習後反思", "trigger_timing": "after_feedback", "questions": QUESTIONS, **body}
    return call(client, db, user, "POST", f"/api/teacher/tools/{TOOL}/forms", json=payload)


def attempt(n, *, score=80, status="passed"):
    return {"id": f"a{n}", "user_id": STUDENT.id, "step_id": STEP, "module_id": MODULE, "attempt_number": n,
            "status": status, "ai_evaluations": [{"id": f"e{n}", "total_score": score}], "teacher_reviews": []}


class TestTeacherApi:

    def test_creator_can_create_form(self, client):
        db = make_db()
        res = new_form(client, db)
        assert res.status_code == 201
        row = db.rows("forms")[0]
        assert row["version"] == 1 and row["form_key"] == row["id"] and row["is_active"]
        assert [q["id"] for q in row["schema_json"]["questions"]] == ["q1", "q2", "q3"]

    def test_other_teacher_cannot_create_or_edit(self, client):
        db = make_db()
        assert new_form(client, db, OTHER_TEACHER).status_code == 403
        form_id = new_form(client, db).json()["data"]["id"]
        res = call(client, db, OTHER_TEACHER, "PUT", f"/api/teacher/forms/{form_id}",
                   json={"title": "改", "questions": QUESTIONS})
        assert res.status_code == 403

    def test_professor_can_create_on_any_tool(self, client):
        assert new_form(client, make_db(), PROF).status_code == 201

    def test_invalid_questions_return_400(self, client):
        res = new_form(client, make_db(), questions=[{"type": "single", "label": "A", "options": ["一"]}])
        assert res.status_code == 400 and res.json()["detail"]["code"] == "FORM_INVALID"

    def test_students_cannot_manage_forms(self, client):
        assert new_form(client, make_db(), STUDENT).status_code == 403

    def test_update_creates_new_version_and_keeps_old(self, client):
        db = make_db()
        v1 = new_form(client, db).json()["data"]
        res = call(client, db, CREATOR, "PUT", f"/api/teacher/forms/{v1['id']}",
                   json={"title": "練習後反思（修訂）", "questions": QUESTIONS[:1]})
        assert res.status_code == 200
        v2 = res.json()["data"]
        assert v2["version"] == 2 and v2["form_key"] == v1["form_key"] and v2["id"] != v1["id"]
        old = next(f for f in db.rows("forms") if f["id"] == v1["id"])
        assert old["is_active"] is False and len(old["schema_json"]["questions"]) == 3

    def test_old_version_cannot_be_edited(self, client):
        db = make_db()
        v1 = new_form(client, db).json()["data"]
        call(client, db, CREATOR, "PUT", f"/api/teacher/forms/{v1['id']}", json={"title": "x", "questions": QUESTIONS})
        res = call(client, db, CREATOR, "PUT", f"/api/teacher/forms/{v1['id']}", json={"title": "y", "questions": QUESTIONS})
        assert res.status_code == 409

    def test_list_shows_only_current_versions_unless_asked(self, client):
        db = make_db()
        v1 = new_form(client, db).json()["data"]
        call(client, db, CREATOR, "PUT", f"/api/teacher/forms/{v1['id']}", json={"title": "x", "questions": QUESTIONS})
        current = call(client, db, CREATOR, "GET", f"/api/teacher/tools/{TOOL}/forms").json()["data"]
        assert [f["version"] for f in current] == [2]
        everything = call(client, db, CREATOR, "GET", f"/api/teacher/tools/{TOOL}/forms?include_old=true").json()
        assert [f["version"] for f in everything["data"]] == [1, 2]

    def test_delete_deactivates_but_keeps_rows(self, client):
        db = make_db()
        v1 = new_form(client, db).json()["data"]
        assert call(client, db, CREATOR, "DELETE", f"/api/teacher/forms/{v1['id']}").status_code == 200
        assert db.rows("forms") and not any(f["is_active"] for f in db.rows("forms"))

    def test_release_only_for_teacher_release_forms(self, client):
        db = make_db()
        plain = new_form(client, db).json()["data"]
        assert call(client, db, CREATOR, "POST", f"/api/teacher/forms/{plain['id']}/release").status_code == 400
        released = new_form(client, db, trigger_timing="teacher_release").json()["data"]
        res = call(client, db, CREATOR, "POST", f"/api/teacher/forms/{released['id']}/release")
        assert res.status_code == 200 and res.json()["data"]["released_at"]
        res = call(client, db, CREATOR, "POST", f"/api/teacher/forms/{released['id']}/release", json={"released": False})
        assert res.json()["data"]["released_at"] is None

    def test_changes_are_audited(self, client):
        db = make_db()
        new_form(client, db)
        assert db.rows("audit_logs")[0]["action"] == "form.create"


class TestStudentApi:

    def pending(self, client, db):
        res = call(client, db, STUDENT, "GET", f"/api/practice/steps/{STEP}/forms")
        assert res.status_code == 200
        return res.json()["data"]

    def submit(self, client, db, form_id, **body):
        return call(client, db, STUDENT, "POST", f"/api/practice/forms/{form_id}/responses",
                    json={"step_id": STEP, **body})

    def test_after_feedback_form_appears_with_latest_attempt(self, client):
        db = make_db(step_attempts=[attempt(1)])
        new_form(client, db)
        data = self.pending(client, db)
        assert len(data["pending"]) == 1 and data["pending"][0]["attempt_id"] == "a1"
        assert data["pending"][0]["questions"][0]["id"] == "q1"

    def test_no_forms_means_nothing_pending(self, client):
        assert self.pending(client, make_db())["pending"] == []

    def test_answer_links_attempt_ai_score_and_course(self, client):
        db = make_db(step_attempts=[attempt(1, score=65, status="revision_required")])
        form_id = new_form(client, db).json()["data"]["id"]
        res = self.submit(client, db, form_id, attempt_id="a1", responses={"answers": {"q1": 4}})
        assert res.status_code == 201
        row = db.rows("form_responses")[0]
        assert row["ai_evaluation_id"] == "e1" and row["ai_total_score"] == 65
        assert row["course_id"] == "course-1" and row["module_id"] == MODULE and row["tool_id"] == TOOL
        assert row["form_version"] == 1 and row["timing"] == "after_feedback" and row["skipped"] is False
        assert self.pending(client, db)["pending"] == []

    def test_same_attempt_cannot_be_answered_twice(self, client):
        db = make_db(step_attempts=[attempt(1)])
        form_id = new_form(client, db).json()["data"]["id"]
        self.submit(client, db, form_id, attempt_id="a1", responses={"answers": {"q1": 4}})
        res = self.submit(client, db, form_id, attempt_id="a1", responses={"answers": {"q1": 5}})
        assert res.status_code == 409

    def test_next_attempt_shows_form_again(self, client):
        db = make_db(step_attempts=[attempt(1)])
        form_id = new_form(client, db).json()["data"]["id"]
        self.submit(client, db, form_id, attempt_id="a1", responses={"answers": {"q1": 4}})
        db.tables["step_attempts"].append(attempt(2))
        assert self.pending(client, db)["pending"][0]["attempt_id"] == "a2"

    def test_optional_form_can_be_skipped_required_cannot(self, client):
        db = make_db()
        optional = new_form(client, db, trigger_timing="pre_practice").json()["data"]["id"]
        required = new_form(client, db, trigger_timing="pre_practice", is_required=True).json()["data"]["id"]
        assert self.submit(client, db, optional, skipped=True).status_code == 201
        res = self.submit(client, db, required, skipped=True)
        assert res.status_code == 400 and res.json()["detail"]["code"] == "FORM_REQUIRED"
        assert [f["id"] for f in self.pending(client, db)["pending"]] == [required]

    def test_attempt_form_needs_attempt_id(self, client):
        db = make_db(step_attempts=[attempt(1)])
        form_id = new_form(client, db).json()["data"]["id"]
        res = self.submit(client, db, form_id, responses={"answers": {"q1": 3}})
        assert res.status_code == 400 and res.json()["detail"]["code"] == "ATTEMPT_REQUIRED"

    def test_cannot_answer_for_someone_elses_attempt(self, client):
        other = {**attempt(1), "user_id": "stu-2"}
        db = make_db(step_attempts=[other])
        form_id = new_form(client, db).json()["data"]["id"]
        assert self.submit(client, db, form_id, attempt_id="a1", responses={"answers": {"q1": 3}}).status_code == 404

    def test_personal_data_in_answer_is_blocked(self, client):
        db = make_db(step_attempts=[attempt(1)])
        form_id = new_form(client, db).json()["data"]["id"]
        res = self.submit(client, db, form_id, attempt_id="a1",
                          responses={"answers": {"q1": 3, "q3": "孩子身分證 A123456789"}})
        assert res.status_code == 400 and res.json()["detail"]["code"] == "PII_DETECTED"
        assert db.rows("form_responses") == []

    def test_old_version_cannot_be_answered(self, client):
        db = make_db(step_attempts=[attempt(1)])
        v1 = new_form(client, db).json()["data"]["id"]
        call(client, db, CREATOR, "PUT", f"/api/teacher/forms/{v1}", json={"title": "x", "questions": QUESTIONS})
        res = self.submit(client, db, v1, attempt_id="a1", responses={"answers": {"q1": 3}})
        assert res.status_code == 409

    def test_old_answers_keep_their_version_after_edit(self, client):
        db = make_db(step_attempts=[attempt(1)])
        v1 = new_form(client, db).json()["data"]["id"]
        self.submit(client, db, v1, attempt_id="a1", responses={"answers": {"q1": 3}})
        call(client, db, CREATOR, "PUT", f"/api/teacher/forms/{v1}", json={"title": "x", "questions": QUESTIONS[2:]})
        listed = call(client, db, CREATOR, "GET", f"/api/teacher/forms/{v1}/responses").json()["data"]
        assert [r["form_id"] for r in listed["responses"]] == [v1]
        assert [v["version"] for v in listed["versions"]] == [1, 2]
        assert listed["responses"][0]["student_name"] == "王小美"
        # 改版不會讓已填過這次作答的人再填
        assert self.pending(client, db)["pending"] == []

    def test_form_from_other_tool_is_rejected(self, client):
        db = make_db()
        form_id = new_form(client, db, trigger_timing="pre_practice").json()["data"]["id"]
        db.tables["forms"][0]["tool_id"] = "tool-other"
        res = self.submit(client, db, form_id, responses={"answers": {"q1": 3}})
        assert res.status_code == 400 and res.json()["detail"]["code"] == "FORM_NOT_FOR_STEP"

    def test_full_feedback_gate_locks_feedback_while_pending(self, client):
        db = make_db(step_attempts=[attempt(1)])
        new_form(client, db, gate="full_feedback")
        assert self.pending(client, db)["feedback_locked"] is True


class TestCompletionGate:

    def gated_db(self, client):
        db = make_db(step_attempts=[attempt(1)])
        form_id = new_form(client, db, gate="completion").json()["data"]["id"]
        return db, form_id

    def test_passed_step_is_not_passed_until_form_answered(self, client):
        db, form_id = self.gated_db(client)
        progress = step_unlock.module_progress(db, STUDENT.id, MODULE)
        assert progress[0]["passed"] is False and progress[0]["form_pending"] is True

        call(client, db, STUDENT, "POST", f"/api/practice/forms/{form_id}/responses",
             json={"step_id": STEP, "attempt_id": "a1", "responses": {"answers": {"q1": 4}}})
        progress = step_unlock.module_progress(db, STUDENT.id, MODULE)
        assert progress[0]["passed"] is True and progress[0]["form_pending"] is False

    def test_skipping_does_not_complete(self, client):
        db, form_id = self.gated_db(client)
        call(client, db, STUDENT, "POST", f"/api/practice/forms/{form_id}/responses",
             json={"step_id": STEP, "attempt_id": "a1", "skipped": True})
        assert steps_blocked_by_forms(db, STUDENT.id, db.rows("module_steps")) == {STEP}

    def test_missing_forms_table_does_not_block(self):
        db = make_db(step_attempts=[attempt(1)])
        db.errors[("forms", "select")] = RuntimeError("relation forms has no column gate")
        assert steps_blocked_by_forms(db, STUDENT.id, db.rows("module_steps")) == set()
        assert step_unlock.module_progress(db, STUDENT.id, MODULE)[0]["passed"] is True
