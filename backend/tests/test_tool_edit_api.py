"""
單元測試：機器人建立、修改與版本（api/teacher.py，migration 022）

用記憶體版 Supabase（tests/inmemory_db.py），不連線、不寫入真的資料庫。
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from api import teacher
from api.practice import _insert_evaluation
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

PROFESSOR = CurrentUser(id="prof-1", role="owner")
CREATOR = CurrentUser(id="assistant-1", role="assistant")
OTHER_ASSISTANT = CurrentUser(id="assistant-2", role="assistant")
STUDENT = CurrentUser(id="student-1", role="student")

RUBRIC = [
    {"dimension": "客觀事實辨識", "max_score": 40, "description": "不加入主觀猜測"},
    {"dimension": "支持需求具體性", "max_score": 60, "description": "說明需要何種支持"},
]

NEW_TOOL = {
    "title": "現況描述教練",
    "domain": "學前IEP",
    "target_competency": "撰寫客觀的現況描述",
    "role_instruction": "你是資深特教督導",
    "system_prompt": "引導學生區分事實與推論",
    "rubric_criteria": RUBRIC,
    "scale_type": "4_point",
    "teaching_strategy": {"memory": {"enabled": True, "max_attempts": 3, "include_prior_steps": True}},
    "error_taxonomy": [],
}


def existing_tool(**overrides) -> dict:
    return {
        "id": "t1",
        **NEW_TOOL,
        "status": "published",
        "version": 1,
        "created_by": CREATOR.id,
        **overrides,
    }


@pytest.fixture(autouse=True)
def reset_login():
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def call(method: str, path: str, db: InMemoryDB, user: CurrentUser = CREATOR, json=None):
    main.app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(teacher, "get_supabase", return_value=db):
        return client.request(method, path, json=json)


# ── 權限規則 ──────────────────────────────────────────────────────────────


class TestCanEditTool:

    def test_creator_can_edit(self):
        assert teacher.can_edit_tool(CREATOR, existing_tool()) is True

    def test_professor_can_edit_any_tool(self):
        assert teacher.can_edit_tool(PROFESSOR, existing_tool()) is True

    def test_other_assistant_cannot_edit(self):
        assert teacher.can_edit_tool(OTHER_ASSISTANT, existing_tool()) is False

    def test_tool_without_creator_only_professor_can_edit(self):
        """建立者帳號被刪除（created_by = NULL）時，只剩教授能改"""
        tool = existing_tool(created_by=None)
        assert teacher.can_edit_tool(OTHER_ASSISTANT, tool) is False
        assert teacher.can_edit_tool(PROFESSOR, tool) is True


# ── 建立 ──────────────────────────────────────────────────────────────────


class TestCreateTool:

    def test_create_success_records_creator_and_first_version(self):
        db = InMemoryDB()
        res = call("POST", "/api/teacher/tools", db, json=NEW_TOOL)

        assert res.status_code == 201
        tool = res.json()["data"]
        assert tool["created_by"] == CREATOR.id
        assert tool["version"] == 1
        assert tool["status"] == "published"

        versions = db.rows("ai_tool_versions")
        assert len(versions) == 1
        assert versions[0]["tool_id"] == tool["id"]
        assert versions[0]["version"] == 1
        assert versions[0]["snapshot"]["system_prompt"] == NEW_TOOL["system_prompt"]

    def test_created_by_cannot_be_forged_from_body(self):
        """body 裡塞 created_by 也不採用，一律是登入者"""
        db = InMemoryDB()
        res = call("POST", "/api/teacher/tools", db, json={**NEW_TOOL, "created_by": "someone-else", "version": 99})

        assert res.status_code == 201
        assert res.json()["data"]["created_by"] == CREATOR.id
        assert res.json()["data"]["version"] == 1

    def test_student_cannot_create(self):
        db = InMemoryDB()
        res = call("POST", "/api/teacher/tools", db, user=STUDENT, json=NEW_TOOL)

        assert res.status_code == 403
        assert db.rows("ai_tools") == []

    def test_blank_title_is_rejected(self):
        res = call("POST", "/api/teacher/tools", InMemoryDB(), json={**NEW_TOOL, "title": "   "})
        assert res.status_code == 422

    def test_tool_without_rubric_can_be_created(self):
        """評分規準可以不填（#65）：不評分、只給文字回饋的機器人"""
        db = InMemoryDB()
        res = call("POST", "/api/teacher/tools", db, json={**NEW_TOOL, "rubric_criteria": []})

        assert res.status_code == 201
        assert res.json()["data"]["rubric_criteria"] == []

    def test_rubric_item_without_name_is_rejected(self):
        body = {**NEW_TOOL, "rubric_criteria": [{"dimension": "  ", "max_score": 10}]}
        res = call("POST", "/api/teacher/tools", InMemoryDB(), json=body)
        assert res.status_code == 422

    def test_duplicate_rubric_dimension_is_rejected(self):
        body = {**NEW_TOOL, "rubric_criteria": [RUBRIC[0], {**RUBRIC[1], "dimension": RUBRIC[0]["dimension"]}]}
        res = call("POST", "/api/teacher/tools", InMemoryDB(), json=body)
        assert res.status_code == 422

    def test_unknown_scale_type_is_rejected(self):
        res = call("POST", "/api/teacher/tools", InMemoryDB(), json={**NEW_TOOL, "scale_type": "10_point"})
        assert res.status_code == 422


# ── 修改 ──────────────────────────────────────────────────────────────────


class TestUpdateTool:

    def test_update_increments_version_and_keeps_old_content(self):
        db = InMemoryDB(ai_tools=[existing_tool()])
        res = call("PATCH", "/api/teacher/tools/t1", db, json={"system_prompt": "新的引導方式"})

        assert res.status_code == 200
        body = res.json()
        assert body["changed"] is True
        assert body["data"]["version"] == 2
        assert body["data"]["system_prompt"] == "新的引導方式"
        assert body["data"]["updated_by"] == CREATOR.id

        # 第 1 版（舊內容）與第 2 版都留著
        versions = {v["version"]: v for v in db.rows("ai_tool_versions")}
        assert set(versions) == {1, 2}
        assert versions[1]["snapshot"]["system_prompt"] == NEW_TOOL["system_prompt"]
        assert versions[2]["snapshot"]["system_prompt"] == "新的引導方式"
        assert versions[2]["edited_by"] == CREATOR.id

    def test_each_update_adds_one_version(self):
        db = InMemoryDB(ai_tools=[existing_tool()])
        call("PATCH", "/api/teacher/tools/t1", db, json={"title": "第二版"})
        res = call("PATCH", "/api/teacher/tools/t1", db, json={"title": "第三版"})

        assert res.json()["data"]["version"] == 3
        assert sorted(v["version"] for v in db.rows("ai_tool_versions")) == [1, 2, 3]

    def test_unchanged_content_does_not_bump_version(self):
        db = InMemoryDB(ai_tools=[existing_tool()])
        res = call("PATCH", "/api/teacher/tools/t1", db, json={"title": NEW_TOOL["title"], "rubric_criteria": RUBRIC})

        assert res.status_code == 200
        assert res.json()["changed"] is False
        assert db.rows("ai_tools")[0]["version"] == 1
        assert db.rows("ai_tool_versions") == []

    def test_other_assistant_gets_403(self):
        db = InMemoryDB(ai_tools=[existing_tool()])
        res = call("PATCH", "/api/teacher/tools/t1", db, user=OTHER_ASSISTANT, json={"title": "偷改"})

        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "PERMISSION_DENIED"
        assert db.rows("ai_tools")[0]["title"] == NEW_TOOL["title"]
        assert db.rows("ai_tools")[0]["version"] == 1

    def test_student_gets_403(self):
        db = InMemoryDB(ai_tools=[existing_tool()])
        res = call("PATCH", "/api/teacher/tools/t1", db, user=STUDENT, json={"title": "偷改"})
        assert res.status_code == 403

    def test_professor_can_edit_someone_elses_tool(self):
        db = InMemoryDB(ai_tools=[existing_tool()])
        res = call("PATCH", "/api/teacher/tools/t1", db, user=PROFESSOR, json={"title": "教授改的"})

        assert res.status_code == 200
        assert res.json()["data"]["updated_by"] == PROFESSOR.id
        # 建立者不變
        assert res.json()["data"]["created_by"] == CREATOR.id

    def test_missing_tool_returns_404(self):
        res = call("PATCH", "/api/teacher/tools/nope", InMemoryDB(), json={"title": "x"})
        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "TOOL_NOT_FOUND"

    def test_stale_expected_version_returns_409(self):
        """老師打開編輯頁後，別人先存了一版 → 不蓋掉別人的修改"""
        db = InMemoryDB(ai_tools=[existing_tool(version=3)])
        res = call("PATCH", "/api/teacher/tools/t1", db, json={"title": "x", "expected_version": 2})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "VERSION_CONFLICT"
        assert db.rows("ai_tools")[0]["version"] == 3

    def test_blank_field_is_rejected(self):
        db = InMemoryDB(ai_tools=[existing_tool()])
        res = call("PATCH", "/api/teacher/tools/t1", db, json={"system_prompt": "  "})
        assert res.status_code == 422

    def test_custom_rubric_levels_are_kept(self):
        """教授自訂的 levels 不在表單上，修改時不能被丟掉"""
        levels = [{"score": 1, "label": "待加強"}, {"score": 2, "label": "達成"}]
        rubric = [{**RUBRIC[0], "levels": levels}, RUBRIC[1]]
        db = InMemoryDB(ai_tools=[existing_tool()])
        res = call("PATCH", "/api/teacher/tools/t1", db, json={"rubric_criteria": rubric})

        assert res.json()["data"]["rubric_criteria"][0]["levels"] == levels


class TestRubricChangeNotice:

    def _db_with_attempts(self, n: int) -> InMemoryDB:
        return InMemoryDB(
            ai_tools=[existing_tool()],
            module_steps=[{"id": "s1", "tool_id": "t1"}, {"id": "s-other", "tool_id": "t-other"}],
            step_attempts=[{"id": f"a{i}", "step_id": "s1"} for i in range(n)]
            + [{"id": "other", "step_id": "s-other"}],
        )

    def test_rubric_change_reports_existing_attempts(self):
        """已有作答時改規準，回傳作答數讓前端提醒「舊的評分不會重算」"""
        new_rubric = [{**RUBRIC[0], "max_score": 50}, {**RUBRIC[1], "max_score": 50}]
        res = call("PATCH", "/api/teacher/tools/t1", self._db_with_attempts(2), json={"rubric_criteria": new_rubric})

        assert res.json()["rubric_changed"] is True
        assert res.json()["attempt_count"] == 2

    def test_scale_type_change_counts_as_rubric_change(self):
        res = call("PATCH", "/api/teacher/tools/t1", self._db_with_attempts(1), json={"scale_type": "5_point"})
        assert res.json()["rubric_changed"] is True

    def test_switching_to_no_scoring_counts_as_rubric_change(self):
        """改成不評分（規準清空）也要提醒：舊的分數還在，不會被拿掉"""
        res = call("PATCH", "/api/teacher/tools/t1", self._db_with_attempts(3), json={"rubric_criteria": []})

        assert res.status_code == 200
        assert res.json()["data"]["rubric_criteria"] == []
        assert res.json()["rubric_changed"] is True
        assert res.json()["attempt_count"] == 3

    def test_prompt_change_is_not_rubric_change(self):
        res = call("PATCH", "/api/teacher/tools/t1", self._db_with_attempts(2), json={"system_prompt": "新的"})
        assert res.json()["rubric_changed"] is False


# ── 查看 ──────────────────────────────────────────────────────────────────


class TestGetTool:

    def test_get_tool_includes_can_edit_and_attempt_count(self):
        db = InMemoryDB(
            ai_tools=[existing_tool()],
            module_steps=[{"id": "s1", "tool_id": "t1"}],
            step_attempts=[{"id": "a1", "step_id": "s1"}],
        )
        res = call("GET", "/api/teacher/tools/t1", db, user=OTHER_ASSISTANT)

        assert res.status_code == 200
        data = res.json()["data"]
        assert data["can_edit"] is False
        assert data["attempt_count"] == 1

    def test_tool_not_used_by_any_step_has_zero_attempts(self):
        res = call("GET", "/api/teacher/tools/t1", InMemoryDB(ai_tools=[existing_tool()]))
        assert res.json()["data"]["attempt_count"] == 0

    def test_get_missing_tool_returns_404(self):
        res = call("GET", "/api/teacher/tools/nope", InMemoryDB())
        assert res.status_code == 404

    def test_student_cannot_read_tool_settings(self):
        res = call("GET", "/api/teacher/tools/t1", InMemoryDB(ai_tools=[existing_tool()]), user=STUDENT)
        assert res.status_code == 403

    def test_list_marks_which_tools_are_editable(self):
        db = InMemoryDB(ai_tools=[existing_tool(), existing_tool(id="t2", created_by=OTHER_ASSISTANT.id)])
        res = call("GET", "/api/teacher/tools", db)

        editable = {t["id"]: t["can_edit"] for t in res.json()["data"]}
        assert editable == {"t1": True, "t2": False}


# ── 評分記下機器人版本 ────────────────────────────────────────────────────


class TestEvaluationToolVersion:

    class _DB:
        """模擬資料庫缺少某些欄位：insert 帶到這些欄位就丟出 PostgREST 的錯誤訊息"""

        def __init__(self, missing: set[str]):
            self.missing = missing
            self.rows: list[dict] = []

        def table(self, name):
            outer = self

            class Q:
                def insert(self, row):
                    self.row = row
                    return self

                def execute(self):
                    for col in outer.missing:
                        if col in self.row:
                            raise RuntimeError(f"Could not find the '{col}' column of 'ai_evaluations'")
                    outer.rows.append(self.row)

            return Q()

    def test_tool_version_is_saved(self):
        db = self._DB(missing=set())
        _insert_evaluation(db, {"attempt_id": "a1", "tool_version": 3, "memory_snapshot": None})
        assert db.rows == [{"attempt_id": "a1", "tool_version": 3, "memory_snapshot": None}]

    def test_without_migration_022_only_tool_version_is_dropped(self):
        """跑了 015 但還沒跑 022：只拿掉 tool_version，記憶快照照存"""
        db = self._DB(missing={"tool_version"})
        _insert_evaluation(db, {"attempt_id": "a1", "tool_version": 3, "memory_snapshot": {"text": "x"}})
        assert db.rows == [{"attempt_id": "a1", "memory_snapshot": {"text": "x"}}]

    def test_without_both_migrations_both_are_dropped(self):
        db = self._DB(missing={"tool_version", "memory_snapshot"})
        _insert_evaluation(db, {"attempt_id": "a1", "tool_version": 3, "memory_snapshot": {"text": "x"}})
        assert db.rows == [{"attempt_id": "a1"}]
