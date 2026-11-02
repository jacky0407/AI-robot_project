"""
單元測試：模組與步驟管理 API (api/modules.py)

用記憶體版資料庫（tests/inmemory_db.py），驗證操作後資料真正的樣子。

重點：
  - 權限：讀取限教師端；新增、修改、刪除只限教授
  - 研究資料保護：有作答的步驟不能刪、不能換機器人；有作答的模組不能刪
  - 排序與刪除後重新編號
  - 發布檢查：至少一個啟用中的步驟，且機器人都已發布
  - 還沒跑 migration 010 時回 503，說明要跑哪一支
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import modules
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

PROFESSOR = CurrentUser(id="prof-1", role="owner")
ASSISTANT = CurrentUser(id="assistant-1", role="assistant")
STUDENT = CurrentUser(id="student-1", role="student")

MOD = "b0000000-0000-4000-8000-000000000001"
TOOL_A = "a0000000-0000-4000-8000-00000000000a"
TOOL_B = "a0000000-0000-4000-8000-00000000000b"
TOOL_DRAFT = "a0000000-0000-4000-8000-0000000000dd"
TOOL_ARCHIVED = "a0000000-0000-4000-8000-0000000000ee"
S1 = "50000000-0000-4000-8000-000000000001"
S2 = "50000000-0000-4000-8000-000000000002"
S3 = "50000000-0000-4000-8000-000000000003"


def make_db(*, attempts=None, steps=None, published=False, assignments=None) -> InMemoryDB:
    return InMemoryDB(
        learning_modules=[{"id": MOD, "title": "學前 IEP", "description": None, "domain": "學前IEP",
                           "is_published": published, "created_at": "2026-11-01"}],
        module_steps=steps if steps is not None else [
            {"id": S1, "module_id": MOD, "tool_id": TOOL_A, "step_order": 1, "step_title": "步驟1",
             "is_required": True, "pass_score": 70, "require_teacher_review": False, "is_active": True},
            {"id": S2, "module_id": MOD, "tool_id": TOOL_B, "step_order": 2, "step_title": "步驟2",
             "is_required": True, "pass_score": 70, "require_teacher_review": False, "is_active": True},
            {"id": S3, "module_id": MOD, "tool_id": TOOL_A, "step_order": 3, "step_title": "步驟3",
             "is_required": False, "pass_score": 60, "require_teacher_review": False, "is_active": True},
        ],
        ai_tools=[
            {"id": TOOL_A, "title": "案例整理教練", "status": "published"},
            {"id": TOOL_B, "title": "功能性現況教練", "status": "published"},
            {"id": TOOL_DRAFT, "title": "草稿教練", "status": "draft"},
            {"id": TOOL_ARCHIVED, "title": "舊教練", "status": "archived"},
        ],
        step_attempts=attempts or [],
        course_modules=assignments or [],
    )


def attempt_on(step_id):
    return {"id": f"att-{step_id}", "module_id": MOD, "step_id": step_id, "user_id": "student-1"}


@pytest.fixture(autouse=True)
def logged_in_professor():
    main.app.dependency_overrides[get_current_user] = lambda: PROFESSOR
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def login_as(user: CurrentUser):
    main.app.dependency_overrides[get_current_user] = lambda: user


def call(method, path, db, json=None):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(modules, "get_supabase", return_value=db):
        return client.request(method, f"/api/modules{path}", json=json)


def orders(db):
    return {r["id"]: r["step_order"] for r in db.rows("module_steps")}


# ──────────────────────────────────────────
# 權限
# ──────────────────────────────────────────

WRITE_CALLS = [
    ("POST", "", {"title": "新模組"}),
    ("PATCH", f"/{MOD}", {"title": "改名"}),
    ("DELETE", f"/{MOD}", None),
    ("POST", f"/{MOD}/steps", {"tool_id": TOOL_A, "step_title": "新步驟"}),
    ("PATCH", f"/{MOD}/steps/{S1}", {"is_active": False}),
    ("DELETE", f"/{MOD}/steps/{S1}", None),
    ("PUT", f"/{MOD}/steps/order", {"step_ids": [S1, S2, S3]}),
]


class TestPermissions:

    def test_without_token_returns_401(self):
        main.app.dependency_overrides.pop(get_current_user, None)
        db = make_db()
        res = call("GET", "", db)

        assert res.status_code == 401
        assert db.calls == []

    @pytest.mark.parametrize("method, path", [("GET", ""), ("GET", f"/{MOD}")])
    def test_student_cannot_read(self, method, path):
        login_as(STUDENT)
        db = make_db()
        res = call(method, path, db)

        assert res.status_code == 403
        assert db.calls == []

    def test_assistant_can_read(self):
        login_as(ASSISTANT)
        assert call("GET", f"/{MOD}", make_db()).status_code == 200

    @pytest.mark.parametrize("method, path, body", WRITE_CALLS)
    def test_assistant_cannot_write(self, method, path, body):
        login_as(ASSISTANT)
        db = make_db()
        res = call(method, path, db, json=body)

        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "PERMISSION_DENIED"
        assert db.calls == []


# ──────────────────────────────────────────
# 模組
# ──────────────────────────────────────────

class TestModules:

    def test_list_counts_active_steps(self):
        db = make_db()
        db.rows("module_steps")[2]["is_active"] = False
        res = call("GET", "", db)

        m = res.json()["data"][0]
        assert (m["step_count"], m["active_step_count"]) == (3, 2)

    def test_create_starts_unpublished(self):
        db = make_db()
        res = call("POST", "", db, json={"title": "  步驟 3–9 模組  ", "description": ""})

        assert res.status_code == 201
        created = res.json()["data"]
        assert created["title"] == "步驟 3–9 模組"
        assert created["is_published"] is False
        assert created["description"] is None

    @pytest.mark.parametrize("body", [{"title": ""}, {"title": "   "}, {"title": "x" * 101}])
    def test_create_rejects_bad_title(self, body):
        assert call("POST", "", make_db(), json=body).status_code == 422

    def test_detail_includes_tool_titles_and_attempt_counts(self):
        db = make_db(attempts=[attempt_on(S1), attempt_on(S1)])
        steps = call("GET", f"/{MOD}", db).json()["data"]["steps"]

        assert [s["step_order"] for s in steps] == [1, 2, 3]
        assert steps[0]["tool_title"] == "案例整理教練"
        assert (steps[0]["attempt_count"], steps[0]["can_delete"]) == (2, False)
        assert (steps[1]["attempt_count"], steps[1]["can_delete"]) == (0, True)

    def test_unknown_module_returns_404(self):
        res = call("GET", "/b0000000-0000-4000-8000-0000000000ff", make_db())
        assert res.json()["detail"]["code"] == "MODULE_NOT_FOUND"

    def test_publish_succeeds_when_ready(self):
        db = make_db()
        res = call("PATCH", f"/{MOD}", db, json={"is_published": True})

        assert res.status_code == 200
        assert db.rows("learning_modules")[0]["is_published"] is True

    def test_publish_requires_an_active_step(self):
        db = make_db(steps=[])
        res = call("PATCH", f"/{MOD}", db, json={"is_published": True})

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "MODULE_EMPTY"
        assert db.rows("learning_modules")[0]["is_published"] is False

    def test_publish_requires_published_tools(self):
        db = make_db()
        db.rows("module_steps")[1]["tool_id"] = TOOL_DRAFT
        res = call("PATCH", f"/{MOD}", db, json={"is_published": True})

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "TOOL_NOT_PUBLISHED"
        assert "草稿教練" in res.json()["detail"]["message"]

    def test_disabled_step_with_draft_tool_does_not_block_publish(self):
        db = make_db()
        db.rows("module_steps")[1].update({"tool_id": TOOL_DRAFT, "is_active": False})
        assert call("PATCH", f"/{MOD}", db, json={"is_published": True}).status_code == 200

    def test_unpublish_skips_checks(self):
        db = make_db(steps=[], published=True)
        assert call("PATCH", f"/{MOD}", db, json={"is_published": False}).status_code == 200

    def test_empty_patch_is_rejected(self):
        assert call("PATCH", f"/{MOD}", make_db(), json={}).json()["detail"]["code"] == "NO_CHANGES"

    def test_module_with_attempts_cannot_be_deleted(self):
        db = make_db(attempts=[attempt_on(S2)])
        res = call("DELETE", f"/{MOD}", db)

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "MODULE_HAS_ATTEMPTS"
        assert len(db.rows("learning_modules")) == 1

    def test_delete_module_without_attempts_reports_unassigned_courses(self):
        db = make_db(assignments=[{"course_id": "c1", "module_id": MOD}, {"course_id": "c2", "module_id": MOD}])
        res = call("DELETE", f"/{MOD}", db)

        assert res.status_code == 200
        assert "2 門課程" in res.json()["message"]
        assert db.rows("learning_modules") == []


# ──────────────────────────────────────────
# 步驟
# ──────────────────────────────────────────

class TestSteps:

    def test_add_step_appends_to_end(self):
        db = make_db()
        res = call("POST", f"/{MOD}/steps", db, json={"tool_id": TOOL_B, "step_title": "步驟4", "pass_score": 80})

        assert res.status_code == 201
        new = [s for s in res.json()["data"]["steps"] if s["step_title"] == "步驟4"][0]
        assert (new["step_order"], new["pass_score"], new["is_required"]) == (4, 80, True)

    def test_add_step_after_disabled_steps_still_appends(self):
        db = make_db()
        db.rows("module_steps")[2]["is_active"] = False
        res = call("POST", f"/{MOD}/steps", db, json={"tool_id": TOOL_B, "step_title": "新"})
        assert max(s["step_order"] for s in res.json()["data"]["steps"]) == 4

    @pytest.mark.parametrize("tool_id, code", [
        (TOOL_ARCHIVED, "TOOL_NOT_AVAILABLE"),
        ("a0000000-0000-4000-8000-0000000000ff", "TOOL_NOT_FOUND"),
    ])
    def test_add_step_rejects_unusable_tool(self, tool_id, code):
        db = make_db()
        res = call("POST", f"/{MOD}/steps", db, json={"tool_id": tool_id, "step_title": "x"})

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == code
        assert len(db.rows("module_steps")) == 3

    @pytest.mark.parametrize("score", [-1, 101])
    def test_pass_score_must_be_0_to_100(self, score):
        res = call("POST", f"/{MOD}/steps", make_db(), json={"tool_id": TOOL_A, "step_title": "x", "pass_score": score})
        assert res.status_code == 422

    def test_draft_tool_can_be_added_before_publishing(self):
        """教授可以先排草稿機器人，發布模組時才檢查"""
        res = call("POST", f"/{MOD}/steps", make_db(), json={"tool_id": TOOL_DRAFT, "step_title": "x"})
        assert res.status_code == 201

    def test_disable_step_keeps_attempts(self):
        db = make_db(attempts=[attempt_on(S1)])
        res = call("PATCH", f"/{MOD}/steps/{S1}", db, json={"is_active": False})

        assert res.status_code == 200
        assert db.rows("module_steps")[0]["is_active"] is False
        assert len(db.rows("step_attempts")) == 1

    def test_update_step_settings(self):
        db = make_db()
        res = call("PATCH", f"/{MOD}/steps/{S2}", db, json={
            "step_title": "步驟2 功能性現況", "is_required": False, "pass_score": 75, "require_teacher_review": True,
        })

        s2 = [s for s in res.json()["data"]["steps"] if s["id"] == S2][0]
        assert (s2["step_title"], s2["is_required"], s2["pass_score"], s2["require_teacher_review"]) == (
            "步驟2 功能性現況", False, 75, True,
        )

    def test_cannot_change_tool_when_step_has_attempts(self):
        db = make_db(attempts=[attempt_on(S1)])
        res = call("PATCH", f"/{MOD}/steps/{S1}", db, json={"tool_id": TOOL_B})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "STEP_HAS_ATTEMPTS"
        assert db.rows("module_steps")[0]["tool_id"] == TOOL_A

    def test_can_change_tool_without_attempts(self):
        db = make_db()
        assert call("PATCH", f"/{MOD}/steps/{S1}", db, json={"tool_id": TOOL_B}).status_code == 200
        assert db.rows("module_steps")[0]["tool_id"] == TOOL_B

    def test_same_tool_is_not_a_change(self):
        db = make_db(attempts=[attempt_on(S1)])
        res = call("PATCH", f"/{MOD}/steps/{S1}", db, json={"tool_id": TOOL_A, "step_title": "改名"})
        assert res.status_code == 200

    def test_step_from_another_module_returns_404(self):
        db = make_db()
        db.rows("module_steps")[0]["module_id"] = "other-module"
        res = call("PATCH", f"/{MOD}/steps/{S1}", db, json={"is_active": False})
        assert res.json()["detail"]["code"] == "STEP_NOT_FOUND"

    def test_step_with_attempts_cannot_be_deleted(self):
        db = make_db(attempts=[attempt_on(S2)])
        res = call("DELETE", f"/{MOD}/steps/{S2}", db)

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "STEP_HAS_ATTEMPTS"
        assert "停用" in res.json()["detail"]["message"]
        assert len(db.rows("module_steps")) == 3

    def test_delete_step_renumbers_remaining(self):
        db = make_db()
        res = call("DELETE", f"/{MOD}/steps/{S2}", db)

        assert res.status_code == 200
        assert orders(db) == {S1: 1, S3: 2}

    def test_delete_still_works_if_renumbering_fails(self):
        db = make_db()
        db.errors[("rpc", "reorder_module_steps")] = RuntimeError("function does not exist")
        res = call("DELETE", f"/{MOD}/steps/{S2}", db)

        assert res.status_code == 200
        assert orders(db) == {S1: 1, S3: 3}


class TestReorder:

    def test_reorder(self):
        db = make_db()
        res = call("PUT", f"/{MOD}/steps/order", db, json={"step_ids": [S3, S1, S2]})

        assert res.status_code == 200
        assert orders(db) == {S3: 1, S1: 2, S2: 3}
        assert [s["id"] for s in res.json()["data"]["steps"]] == [S3, S1, S2]

    @pytest.mark.parametrize("ids", [[S1, S2], [S1, S2, S3, S3], [S1, S1, S2]])
    def test_reorder_requires_exactly_the_module_steps(self, ids):
        db = make_db()
        res = call("PUT", f"/{MOD}/steps/order", db, json={"step_ids": ids})

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "INVALID_ORDER"
        assert db.rpc_calls == []

    def test_reorder_before_migration_010_returns_503(self):
        db = make_db()
        db.errors[("rpc", "reorder_module_steps")] = APIError(
            {"code": "PGRST202", "message": "function not found", "details": "", "hint": None}
        )
        res = call("PUT", f"/{MOD}/steps/order", db, json={"step_ids": [S3, S1, S2]})

        assert res.status_code == 503
        assert "010_module_management.sql" in res.json()["detail"]["message"]


class TestBeforeMigration010:

    def test_missing_is_active_column_returns_503_with_instructions(self):
        db = make_db()
        db.errors[("module_steps", "select")] = APIError(
            {"code": "42703", "message": "column module_steps.is_active does not exist", "details": "", "hint": None}
        )
        res = call("GET", "", db)

        assert res.status_code == 503
        assert res.json()["detail"]["code"] == "MIGRATION_REQUIRED"
