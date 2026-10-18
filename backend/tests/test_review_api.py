"""
單元測試：教師複核 API (api/review.py)

測試策略：
  - 用 FakeSupabase 取代真實資料庫，不連線、不寫入
  - 重點：AI 初評的訊號（信心值、escalate、強制審核點）要讓老師看得到，
    且教師判定永遠不覆蓋 ai_evaluations
"""

from unittest.mock import patch

from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import review


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table: str, db: "FakeSupabase"):
        self.table = table
        self.db = db
        self.op = None
        self.payload = None
        self.selected = ""
        self.filters: list[tuple] = []

    def select(self, *args, **kwargs):
        self.op = "select"
        self.selected = args[0] if args else ""
        return self

    def insert(self, payload, **kwargs):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload, **kwargs):
        self.op, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        self.db.calls.append({"table": self.table, "op": self.op, "selected": self.selected,
                              "payload": self.payload, "filters": self.filters})
        # 模擬還沒跑 migration 003 的資料庫：選 tutor_action 會被 PostgREST 拒絕
        if self.db.without_tutor_action and "tutor_action" in self.selected:
            raise APIError({"code": "42703", "details": None, "hint": None,
                            "message": "column step_attempts.tutor_action does not exist"})
        return FakeResult(self.db.responses.get((self.table, self.op), []))


class FakeSupabase:
    def __init__(self, responses: dict, *, without_tutor_action: bool = False):
        self.responses = responses
        self.without_tutor_action = without_tutor_action
        self.calls: list[dict] = []

    def table(self, name):
        return FakeQuery(name, self)

    def find(self, table, op):
        return [c for c in self.calls if c["table"] == table and c["op"] == op]


def attempt(attempt_id, *, evaluation=None, reviews=None, tutor_action="evaluate",
            require_review=False, status="revision_required"):
    return {
        "id": attempt_id,
        "created_at": "2026-10-01T00:00:00Z",
        "status": status,
        "attempt_number": 1,
        "user_id": "student-1",
        "user_input_content": "作答",
        "tutor_action": tutor_action,
        "profiles": {"full_name": "測試學生", "email": "s@example.com"},
        "module_steps": {"step_title": "步驟1", "tool_id": "tool-1",
                         "require_teacher_review": require_review,
                         "ai_tools": {"title": "教練"}},
        "ai_evaluations": [evaluation] if evaluation else [],
        "teacher_reviews": reviews or [],
    }


EVAL_OK = {"id": "e1", "total_score": 80, "feedback_text": "好",
           "confidence": 0.9, "needs_teacher_review": False}
EVAL_UNSURE = {"id": "e2", "total_score": 55, "feedback_text": "?",
               "confidence": 0.4, "needs_teacher_review": True}


def get(path, fake_db, **params):
    client = TestClient(main.app)
    with patch.object(review, "get_supabase", return_value=fake_db):
        return client.get(path, params=params)


def post(path, fake_db, body):
    client = TestClient(main.app)
    with patch.object(review, "get_supabase", return_value=fake_db):
        return client.post(path, json=body)


# ──────────────────────────────────────────
# GET /api/review/pending
# ──────────────────────────────────────────

class TestPendingQueue:

    def test_escalated_attempt_without_evaluation_is_listed(self):
        """
        回歸：TutorAgent 建議找老師時不評分，沒有 ai_evaluations，
        舊版佇列只列有 AI 初評的作答 → 最需要老師的學生反而看不到。
        """
        db = FakeSupabase({("step_attempts", "select"): [
            attempt("a-esc", tutor_action="escalate"),
        ]})
        data = get("/api/review/pending", db).json()["data"]

        assert [d["submission_id"] for d in data] == ["a-esc"]
        assert data[0]["review_reasons"] == ["escalated"]
        assert data[0]["ai_total_score"] is None

    def test_hint_only_attempt_is_not_listed(self):
        """只拿到提示、沒評分也沒轉介的作答不需要老師處理"""
        db = FakeSupabase({("step_attempts", "select"): [
            attempt("a-hint", tutor_action="give_hint"),
        ]})
        assert get("/api/review/pending", db).json()["data"] == []

    def test_reviewed_attempt_is_not_listed(self):
        db = FakeSupabase({("step_attempts", "select"): [
            attempt("a-done", evaluation=EVAL_OK, reviews=[{"id": "r1"}]),
            attempt("a-esc-done", tutor_action="escalate", reviews=[{"id": "r2"}]),
        ]})
        assert get("/api/review/pending", db).json()["data"] == []

    def test_low_confidence_and_required_step_are_flagged_and_sorted_first(self):
        db = FakeSupabase({("step_attempts", "select"): [
            attempt("a-normal", evaluation=EVAL_OK),               # 最新，但沒有旗標
            attempt("a-unsure", evaluation=EVAL_UNSURE),
            attempt("a-required", evaluation=EVAL_OK, require_review=True),
        ]})
        data = get("/api/review/pending", db).json()["data"]

        assert [d["submission_id"] for d in data] == ["a-unsure", "a-required", "a-normal"]
        assert data[0]["review_reasons"] == ["low_confidence"]
        assert data[0]["ai_confidence"] == 0.4
        assert data[1]["review_reasons"] == ["step_requires_review"]
        assert data[2]["review_reasons"] == []

    def test_works_before_migration_003(self):
        """
        回歸（實測發現）：資料庫還沒有 tutor_action 欄位時，整個待複核清單回 500。
        應退回不含 tutor_action 的查詢，佇列照常運作。
        """
        row = attempt("a-unsure", evaluation=EVAL_UNSURE)
        del row["tutor_action"]
        db = FakeSupabase({("step_attempts", "select"): [row]}, without_tutor_action=True)

        res = get("/api/review/pending", db)

        assert res.status_code == 200
        assert [d["submission_id"] for d in res.json()["data"]] == ["a-unsure"]
        assert "tutor_action" not in db.calls[-1]["selected"]

    def test_needs_attention_filter_still_works(self):
        db = FakeSupabase({("step_attempts", "select"): [
            attempt("a-pass", evaluation=EVAL_OK, status="passed"),
            attempt("a-fail", evaluation=EVAL_UNSURE),
        ]})
        data = get("/api/review/pending", db, needs_attention=True).json()["data"]

        assert [d["submission_id"] for d in data] == ["a-fail"]


# ──────────────────────────────────────────
# GET /api/review/submissions/{id}
# ──────────────────────────────────────────

class TestSubmissionDetail:

    def test_detail_includes_ai_signals(self):
        evaluation = {**EVAL_UNSURE, "dimension_scores": [], "evidence_text": "",
                      "detected_errors": [{"code": "diagnosis_only"}]}
        row = attempt("a1", evaluation=evaluation, require_review=True)
        row["module_steps"]["pass_score"] = 70
        db = FakeSupabase({("step_attempts", "select"): [row]})

        data = get("/api/review/submissions/a1", db).json()["data"]

        assert data["ai_evaluation"]["confidence"] == 0.4
        assert data["ai_evaluation"]["needs_teacher_review"] is True
        assert data["ai_evaluation"]["detected_errors"] == [{"code": "diagnosis_only"}]
        assert data["step"]["require_teacher_review"] is True
        assert data["tutor_action"] == "evaluate"

    def test_detail_without_evaluation_has_safe_defaults(self):
        db = FakeSupabase({("step_attempts", "select"): [
            attempt("a-esc", tutor_action="escalate"),
        ]})
        data = get("/api/review/submissions/a-esc", db).json()["data"]

        assert data["ai_evaluation"]["total_score"] is None
        assert data["ai_evaluation"]["detected_errors"] == []
        assert data["ai_evaluation"]["needs_teacher_review"] is False

    def test_missing_submission_returns_404(self):
        db = FakeSupabase({("step_attempts", "select"): []})
        assert get("/api/review/submissions/nope", db).status_code == 404


# ──────────────────────────────────────────
# POST /api/review/submissions/{id}/judge
# ──────────────────────────────────────────

class TestJudge:

    BODY = {"teacher_id": "teacher-1", "decision": "override",
            "final_score": 85, "final_feedback": "寫得很具體"}

    def test_judge_writes_teacher_review_and_never_touches_ai_evaluations(self):
        db = FakeSupabase({
            ("step_attempts", "select"): [{"id": "a1"}],
            ("teacher_reviews", "insert"): [{"id": "r1", "decision": "override"}],
        })
        res = post("/api/review/submissions/a1/judge", db, self.BODY)

        assert res.status_code == 201
        assert db.find("teacher_reviews", "insert")[0]["payload"]["final_score"] == 85
        assert db.find("ai_evaluations", "update") == []
        assert db.find("ai_evaluations", "insert") == []

    def test_invalid_decision_is_rejected(self):
        db = FakeSupabase({})
        res = post("/api/review/submissions/a1/judge", db,
                   {**self.BODY, "decision": "approve"})

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "INVALID_DECISION"
        assert db.calls == []

    def test_request_retry_sets_revision_required(self):
        db = FakeSupabase({
            ("step_attempts", "select"): [{"id": "a1"}],
            ("teacher_reviews", "insert"): [{"id": "r1"}],
        })
        post("/api/review/submissions/a1/judge", db,
             {**self.BODY, "decision": "request_retry"})

        assert db.find("step_attempts", "update")[0]["payload"] == {"status": "revision_required"}
