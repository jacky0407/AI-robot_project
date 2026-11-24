"""
單元測試：複製與範本（#89，core/templates.py、api/templates.py，以及 teacher／modules 的 copy）

用記憶體版 Supabase（tests/inmemory_db.py），不連線、不寫入真的資料庫。

重點：
  - 複製機器人：新的一支第 1 版、草稿、建立者是自己，內容一樣但和原本的互不影響
  - 複製模組：只複製啟用中的關卡、順序重編、未發布；封存或刪除的機器人關卡略過；聊天室機器人一起複製
  - 範本：存的是當時的副本（原本的之後改了，範本不變）；從範本建立；權限；刪除
  - 學生作答、課程指派都不會被複製
"""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import modules, teacher, templates
from core import templates as core
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

PROFESSOR = CurrentUser(id="prof-1", role="owner")
ASSISTANT = CurrentUser(id="assistant-1", role="assistant")
OTHER_ASSISTANT = CurrentUser(id="assistant-2", role="assistant")
STUDENT = CurrentUser(id="student-1", role="student")

TOOL_A = "a0000000-0000-4000-8000-00000000000a"
TOOL_B = "a0000000-0000-4000-8000-00000000000b"
TOOL_CHAT = "a0000000-0000-4000-8000-00000000000c"
TOOL_ARCHIVED = "a0000000-0000-4000-8000-0000000000ee"
TOOL_GONE = "a0000000-0000-4000-8000-0000000000ff"
MOD = "b0000000-0000-4000-8000-000000000001"
S1 = "50000000-0000-4000-8000-000000000001"
S2 = "50000000-0000-4000-8000-000000000002"
S3 = "50000000-0000-4000-8000-000000000003"
S4 = "50000000-0000-4000-8000-000000000004"
TPL = "c0000000-0000-4000-8000-000000000001"

RUBRIC = [
    {"dimension": "客觀事實辨識", "max_score": 40, "description": "不加入主觀猜測",
     "levels": [{"score": 4, "text": "完全客觀"}]},
    {"dimension": "支持需求具體性", "max_score": 60, "description": "說明需要何種支持"},
]


def tool_row(tool_id=TOOL_A, **overrides) -> dict:
    return {
        "id": tool_id,
        "title": "現況描述教練",
        "domain": "學前IEP",
        "target_competency": "撰寫客觀的現況描述",
        "role_instruction": "你是資深特教督導",
        "system_prompt": "引導學生區分事實與推論",
        "teaching_strategy": {"hints": ["先找事實"], "max_auto_hints": 2},
        "rubric_criteria": RUBRIC,
        "scale_type": "4_point",
        "error_taxonomy": [{"code": "E1", "label": "主觀推論"}],
        "status": "published",
        "version": 5,
        "created_by": PROFESSOR.id,
        "created_at": "2026-11-01",
        "updated_by": PROFESSOR.id,
        **overrides,
    }


def step(step_id, order, tool_id, **overrides) -> dict:
    return {
        "id": step_id, "module_id": MOD, "tool_id": tool_id, "step_order": order,
        "step_title": f"步驟{order}", "is_required": True, "pass_score": 70,
        "require_teacher_review": False, "is_active": True, "pass_mode": "score", "step_type": "answer",
        **overrides,
    }


def make_db(**extra) -> InMemoryDB:
    tables = dict(
        ai_tools=[
            tool_row(TOOL_A),
            tool_row(TOOL_B, title="功能性現況教練"),
            tool_row(TOOL_CHAT, title="討論夥伴"),
            tool_row(TOOL_ARCHIVED, title="舊教練", status="archived"),
        ],
        learning_modules=[{"id": MOD, "title": "學前 IEP", "description": "逐步撰寫", "domain": "學前IEP",
                           "is_published": True, "created_at": "2026-11-01"}],
        module_steps=[
            step(S1, 1, TOOL_A, pass_mode="complete"),
            step(S2, 2, TOOL_B, is_active=False),                       # 停用：不複製
            step(S3, 3, TOOL_B, step_type="chat", is_required=False, pass_score=60),
            step(S4, 4, TOOL_ARCHIVED),                                  # 機器人已封存：略過
        ],
        chat_room_bots=[
            {"id": "bot-1", "step_id": S3, "tool_id": TOOL_CHAT, "display_name": "夥伴",
             "when_to_use": "學生想討論時", "sort_order": 1, "use_materials": True},
            {"id": "bot-2", "step_id": S3, "tool_id": TOOL_ARCHIVED, "display_name": "老教練",
             "when_to_use": "", "sort_order": 2, "use_materials": False},
        ],
        step_attempts=[{"id": "att-1", "module_id": MOD, "step_id": S1, "user_id": "student-1"}],
        course_modules=[{"id": "cm-1", "course_id": "course-1", "module_id": MOD}],
        ai_tool_versions=[],
        templates=[],
    )
    tables.update(extra)
    return InMemoryDB(**tables)


@pytest.fixture(autouse=True)
def reset_login():
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def call(method: str, path: str, db: InMemoryDB, user: CurrentUser = PROFESSOR, json=None):
    main.app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(main.app, raise_server_exceptions=False)
    with ExitStack() as stack:
        for mod in (teacher, modules, templates):
            stack.enter_context(patch.object(mod, "get_supabase", return_value=db))
        return client.request(method, path, json=json)


def new_rows(db: InMemoryDB, table: str, existing_ids: set[str]) -> list[dict]:
    return [r for r in db.rows(table) if r["id"] not in existing_ids]


# ── core：標題與內容 ────────────────────────────────────────────────────────


class TestCopyTitle:

    def test_copy_title_adds_suffix(self):
        assert core.copy_title("現況描述教練") == "現況描述教練（複本）"

    def test_long_title_keeps_suffix_within_limit(self):
        title = core.copy_title("字" * 100)
        assert len(title) == core.TITLE_MAX
        assert title.endswith(core.COPY_SUFFIX)

    def test_blank_title_gets_placeholder(self):
        assert core.copy_title("  ") == "未命名（複本）"

    def test_pick_title_prefers_requested(self):
        assert core.pick_title("  新名稱 ", "預設") == "新名稱"
        assert core.pick_title(None, "預設") == "預設"
        assert core.pick_title("   ", "預設") == "預設"


class TestToolContent:

    def test_only_content_fields_are_kept(self):
        content = core.tool_content(tool_row())
        assert set(content) == set(core.TOOL_CONTENT_FIELDS)
        assert "id" not in content and "version" not in content and "created_by" not in content

    def test_content_is_a_deep_copy(self):
        source = tool_row()
        content = core.tool_content(source)
        content["rubric_criteria"][0]["dimension"] = "改掉"
        assert source["rubric_criteria"][0]["dimension"] == "客觀事實辨識"

    def test_new_tool_row_starts_at_version_one_as_draft(self):
        row = core.new_tool_row(tool_row(), user_id=ASSISTANT.id, title="新的")
        assert row["version"] == 1
        assert row["status"] == "draft"
        assert row["created_by"] == ASSISTANT.id
        assert row["title"] == "新的"

    def test_check_tool_content_rejects_missing_fields(self):
        with pytest.raises(core.TemplateError) as e:
            core.check_tool_content({"title": "只有名稱"})
        assert e.value.code == "TEMPLATE_INVALID"

    def test_check_tool_content_rejects_non_dict(self):
        with pytest.raises(core.TemplateError):
            core.check_tool_content(["不是內容"])


class TestModuleContent:

    def test_only_active_steps_in_order(self):
        db = make_db()
        content = core.module_content(db.rows("learning_modules")[0], db.rows("module_steps"), db.rows("chat_room_bots"))
        assert [s["step_title"] for s in content["steps"]] == ["步驟1", "步驟3", "步驟4"]
        assert "id" not in content["steps"][0]  # 不帶原本的 id
        assert content["title"] == "學前 IEP"

    def test_chat_bots_attached_only_to_chat_steps(self):
        db = make_db()
        content = core.module_content(db.rows("learning_modules")[0], db.rows("module_steps"), db.rows("chat_room_bots"))
        chat = content["steps"][1]
        assert [b["display_name"] for b in chat["chat_bots"]] == ["夥伴", "老教練"]
        assert content["steps"][0]["chat_bots"] == []

    def test_module_tool_ids_unique_in_order(self):
        content = {"steps": [{"tool_id": "a", "chat_bots": [{"tool_id": "b"}, {"tool_id": "a"}]}, {"tool_id": "b"}]}
        assert core.module_tool_ids(content) == ["a", "b"]

    def test_plan_skips_steps_with_archived_or_missing_tools(self):
        content = {"steps": [
            {"step_title": "可以", "tool_id": "ok", "chat_bots": []},
            {"step_title": "封存", "tool_id": "old", "chat_bots": []},
            {"step_title": "刪掉了", "tool_id": "gone", "chat_bots": []},
        ]}
        tools = {"ok": {"status": "published"}, "old": {"status": "archived"}}
        planned, skipped = core.plan_module_copy(content, tools)
        assert [s["step_title"] for s in planned] == ["可以"]
        assert skipped == ["封存", "刪掉了"]

    def test_plan_drops_unusable_chat_bots_but_keeps_step(self):
        content = {"steps": [{"step_title": "聊天", "tool_id": "ok", "step_type": "chat", "chat_bots": [
            {"tool_id": "bot", "display_name": "夥伴"},
            {"tool_id": "old", "display_name": "老教練"},
            {"tool_id": "ok", "display_name": "評分者自己"},   # 評分機器人不能重複加入
        ]}]}
        tools = {"ok": {"status": "published"}, "bot": {"status": "draft"}, "old": {"status": "archived"}}
        planned, skipped = core.plan_module_copy(content, tools)
        assert skipped == []
        assert [b["display_name"] for b in planned[0]["chat_bots"]] == ["夥伴"]


# ── 複製機器人 ─────────────────────────────────────────────────────────────


class TestCopyTool:

    def test_copy_creates_draft_version_one_owned_by_copier(self):
        db = make_db()
        before = {r["id"] for r in db.rows("ai_tools")}
        res = call("POST", f"/api/teacher/tools/{TOOL_A}/copy", db, user=ASSISTANT)

        assert res.status_code == 201
        created = new_rows(db, "ai_tools", before)
        assert len(created) == 1
        tool = created[0]
        assert tool["title"] == "現況描述教練（複本）"
        assert tool["version"] == 1
        assert tool["status"] == "draft"
        assert tool["created_by"] == ASSISTANT.id
        assert tool["rubric_criteria"] == RUBRIC          # 自訂的 levels 也保留
        assert tool["system_prompt"] == "引導學生區分事實與推論"
        assert res.json()["data"]["can_edit"] is True

    def test_copy_saves_version_one_snapshot(self):
        db = make_db()
        res = call("POST", f"/api/teacher/tools/{TOOL_A}/copy", db, user=ASSISTANT)
        new_id = res.json()["data"]["id"]
        versions = [v for v in db.rows("ai_tool_versions") if v["tool_id"] == new_id]
        assert [v["version"] for v in versions] == [1]
        assert versions[0]["edited_by"] == ASSISTANT.id

    def test_copy_with_custom_title(self):
        db = make_db()
        res = call("POST", f"/api/teacher/tools/{TOOL_A}/copy", db, json={"title": "給大二用的版本"})
        assert res.json()["data"]["title"] == "給大二用的版本"

    def test_copy_does_not_change_original(self):
        db = make_db()
        call("POST", f"/api/teacher/tools/{TOOL_A}/copy", db)
        original = next(t for t in db.rows("ai_tools") if t["id"] == TOOL_A)
        assert original["version"] == 5 and original["status"] == "published"

    def test_assistant_can_copy_tool_they_cannot_edit(self):
        """不能改別人的機器人，但可以複製一份自己的來改"""
        db = make_db()
        res = call("POST", f"/api/teacher/tools/{TOOL_A}/copy", db, user=OTHER_ASSISTANT)
        assert res.status_code == 201
        assert res.json()["data"]["created_by"] == OTHER_ASSISTANT.id

    def test_student_cannot_copy(self):
        db = make_db()
        res = call("POST", f"/api/teacher/tools/{TOOL_A}/copy", db, user=STUDENT)
        assert res.status_code == 403

    def test_copy_missing_tool_returns_404(self):
        res = call("POST", f"/api/teacher/tools/{TOOL_GONE}/copy", make_db())
        assert res.status_code == 404

    def test_copy_title_too_long_returns_422(self):
        res = call("POST", f"/api/teacher/tools/{TOOL_A}/copy", make_db(), json={"title": "字" * 101})
        assert res.status_code == 422


# ── 複製模組 ──────────────────────────────────────────────────────────────


class TestCopyModule:

    def test_copy_module_creates_unpublished_module_with_active_steps(self):
        db = make_db()
        res = call("POST", f"/api/modules/{MOD}/copy", db)

        assert res.status_code == 201
        body = res.json()
        new_id = body["data"]["id"]
        assert new_id != MOD
        assert body["data"]["title"] == "學前 IEP（複本）"
        assert body["data"]["is_published"] is False
        assert body["data"]["description"] == "逐步撰寫"

        steps = sorted((s for s in db.rows("module_steps") if s["module_id"] == new_id), key=lambda s: s["step_order"])
        assert [(s["step_order"], s["step_title"], s["tool_id"]) for s in steps] == [
            (1, "步驟1", TOOL_A),
            (2, "步驟3", TOOL_B),
        ]
        assert steps[0]["pass_mode"] == "complete"
        assert steps[1]["step_type"] == "chat"
        assert steps[1]["is_required"] is False and steps[1]["pass_score"] == 60

    def test_copy_module_reports_skipped_archived_steps(self):
        res = call("POST", f"/api/modules/{MOD}/copy", make_db())
        body = res.json()
        assert body["skipped_steps"] == ["步驟4"]
        assert "步驟4" in body["message"]

    def test_copy_module_copies_usable_chat_bots(self):
        db = make_db()
        res = call("POST", f"/api/modules/{MOD}/copy", db)
        new_id = res.json()["data"]["id"]
        chat_step = next(s for s in db.rows("module_steps") if s["module_id"] == new_id and s.get("step_type") == "chat")
        bots = [b for b in db.rows("chat_room_bots") if b["step_id"] == chat_step["id"]]
        assert [(b["tool_id"], b["display_name"], b["use_materials"]) for b in bots] == [(TOOL_CHAT, "夥伴", True)]

    def test_copy_module_does_not_copy_attempts_or_assignments(self):
        db = make_db()
        call("POST", f"/api/modules/{MOD}/copy", db)
        assert len(db.rows("step_attempts")) == 1
        assert len(db.rows("course_modules")) == 1

    def test_copy_module_does_not_copy_tools(self):
        db = make_db()
        call("POST", f"/api/modules/{MOD}/copy", db)
        assert len(db.rows("ai_tools")) == 4

    def test_default_steps_do_not_write_new_columns(self):
        """pass_mode / step_type 是預設值時不寫，還沒跑 019／020 的資料庫照樣能複製"""
        db = make_db()
        res = call("POST", f"/api/modules/{MOD}/copy", db)
        new_id = res.json()["data"]["id"]
        first = next(s for s in db.rows("module_steps") if s["module_id"] == new_id and s["step_order"] == 1)
        assert "step_type" not in first

    def test_assistant_cannot_copy_module(self):
        res = call("POST", f"/api/modules/{MOD}/copy", make_db(), user=ASSISTANT)
        assert res.status_code == 403

    def test_copy_missing_module_returns_404(self):
        res = call("POST", "/api/modules/b0000000-0000-4000-8000-0000000000ff/copy", make_db())
        assert res.status_code == 404

    def test_failure_midway_removes_half_built_module(self):
        db = make_db()
        db.errors[("chat_room_bots", "insert")] = RuntimeError("db down")
        before = {m["id"] for m in db.rows("learning_modules")}
        res = call("POST", f"/api/modules/{MOD}/copy", db)
        assert res.status_code == 500
        assert {m["id"] for m in db.rows("learning_modules")} == before

    def test_module_without_chat_tables_still_copies(self):
        """還沒跑 migration 020：讀不到 chat_room_bots 也照樣複製"""
        db = make_db(module_steps=[step(S1, 1, TOOL_A)])
        db.errors[("chat_room_bots", "select")] = APIError({"code": "42P01", "message": "relation does not exist"})
        res = call("POST", f"/api/modules/{MOD}/copy", db)
        assert res.status_code == 201


# ── 範本 ─────────────────────────────────────────────────────────────────


class TestSaveTemplate:

    def test_save_tool_template_stores_snapshot(self):
        db = make_db()
        res = call("POST", "/api/templates", db, user=ASSISTANT,
                   json={"kind": "tool", "source_id": TOOL_A, "description": "現況描述的起手式"})
        assert res.status_code == 201
        t = db.rows("templates")[0]
        assert t["kind"] == "tool"
        assert t["title"] == "現況描述教練"
        assert t["source_id"] == TOOL_A and t["source_version"] == 5
        assert t["created_by"] == ASSISTANT.id
        assert t["content"]["rubric_criteria"] == RUBRIC
        assert "id" not in t["content"]
        assert res.json()["data"]["can_delete"] is True

    def test_template_unchanged_when_original_tool_is_edited_later(self):
        db = make_db()
        call("POST", "/api/templates", db, json={"kind": "tool", "source_id": TOOL_A})
        original = next(t for t in db.rows("ai_tools") if t["id"] == TOOL_A)
        original["rubric_criteria"][0]["dimension"] = "之後改掉的名稱"
        assert db.rows("templates")[0]["content"]["rubric_criteria"][0]["dimension"] == "客觀事實辨識"

    def test_save_module_template(self):
        db = make_db()
        res = call("POST", "/api/templates", db, json={"kind": "module", "source_id": MOD, "title": "IEP 模組範本"})
        assert res.status_code == 201
        t = db.rows("templates")[0]
        assert t["title"] == "IEP 模組範本"
        assert [s["step_title"] for s in t["content"]["steps"]] == ["步驟1", "步驟3", "步驟4"]

    def test_assistant_cannot_save_module_template(self):
        res = call("POST", "/api/templates", make_db(), user=ASSISTANT, json={"kind": "module", "source_id": MOD})
        assert res.status_code == 403

    def test_student_cannot_save_template(self):
        res = call("POST", "/api/templates", make_db(), user=STUDENT, json={"kind": "tool", "source_id": TOOL_A})
        assert res.status_code == 403

    def test_save_template_of_missing_tool_returns_404(self):
        res = call("POST", "/api/templates", make_db(), json={"kind": "tool", "source_id": TOOL_GONE})
        assert res.status_code == 404

    def test_invalid_kind_returns_422(self):
        res = call("POST", "/api/templates", make_db(), json={"kind": "case", "source_id": TOOL_A})
        assert res.status_code == 422

    def test_missing_migration_returns_503(self):
        db = make_db()
        db.errors[("templates", "insert")] = APIError({"code": "PGRST205", "message": "table not found"})
        res = call("POST", "/api/templates", db, json={"kind": "tool", "source_id": TOOL_A})
        assert res.status_code == 503
        assert "023_templates.sql" in res.json()["detail"]["message"]


def tool_template(**overrides) -> dict:
    return {"id": TPL, "kind": "tool", "title": "現況描述範本", "description": None,
            "content": core.tool_content(tool_row()), "source_id": TOOL_A, "source_version": 5,
            "created_by": ASSISTANT.id, "created_at": "2026-11-20", **overrides}


def module_template(**overrides) -> dict:
    db = make_db()
    content = core.module_content(db.rows("learning_modules")[0], db.rows("module_steps"), db.rows("chat_room_bots"))
    return {"id": TPL, "kind": "module", "title": "IEP 模組範本", "description": None, "content": content,
            "source_id": MOD, "source_version": None, "created_by": PROFESSOR.id, "created_at": "2026-11-20",
            **overrides}


class TestListTemplates:

    def test_list_filters_by_kind_newest_first(self):
        db = make_db(templates=[
            tool_template(id="t-old", created_at="2026-11-01"),
            tool_template(id="t-new", created_at="2026-11-20"),
            module_template(id="m-1"),
        ])
        res = call("GET", "/api/templates?kind=tool", db, user=ASSISTANT)
        assert res.status_code == 200
        assert [t["id"] for t in res.json()["data"]] == ["t-new", "t-old"]

    def test_can_delete_flag(self):
        db = make_db(templates=[tool_template(created_by=ASSISTANT.id)])
        own = call("GET", "/api/templates", db, user=ASSISTANT).json()["data"][0]
        other = call("GET", "/api/templates", db, user=OTHER_ASSISTANT).json()["data"][0]
        prof = call("GET", "/api/templates", db, user=PROFESSOR).json()["data"][0]
        assert (own["can_delete"], other["can_delete"], prof["can_delete"]) == (True, False, True)

    def test_student_cannot_list(self):
        res = call("GET", "/api/templates", make_db(), user=STUDENT)
        assert res.status_code == 403


class TestUseTemplate:

    def test_use_tool_template_creates_draft_tool(self):
        db = make_db(templates=[tool_template()])
        before = {r["id"] for r in db.rows("ai_tools")}
        res = call("POST", f"/api/templates/{TPL}/use", db, user=OTHER_ASSISTANT)
        assert res.status_code == 201
        assert res.json()["kind"] == "tool"
        tool = new_rows(db, "ai_tools", before)[0]
        assert tool["title"] == "現況描述教練"
        assert (tool["version"], tool["status"], tool["created_by"]) == (1, "draft", OTHER_ASSISTANT.id)
        assert tool["rubric_criteria"] == RUBRIC

    def test_use_tool_template_with_new_title(self):
        db = make_db(templates=[tool_template()])
        res = call("POST", f"/api/templates/{TPL}/use", db, json={"title": "我的新機器人"})
        assert res.json()["data"]["title"] == "我的新機器人"

    def test_use_module_template_creates_module(self):
        db = make_db(templates=[module_template()])
        res = call("POST", f"/api/templates/{TPL}/use", db)
        assert res.status_code == 201
        body = res.json()
        assert body["kind"] == "module"
        assert body["data"]["title"] == "學前 IEP"
        assert body["data"]["is_published"] is False
        assert [s["step_title"] for s in body["data"]["steps"]] == ["步驟1", "步驟3"]
        assert body["skipped_steps"] == ["步驟4"]

    def test_module_template_survives_deleted_tool(self):
        """範本存好之後機器人被刪掉：那一關略過，其他照常建立"""
        db = make_db(templates=[module_template()])
        db.tables["ai_tools"] = [t for t in db.rows("ai_tools") if t["id"] != TOOL_A]
        res = call("POST", f"/api/templates/{TPL}/use", db)
        assert res.status_code == 201
        assert res.json()["skipped_steps"] == ["步驟1", "步驟4"]

    def test_assistant_cannot_use_module_template(self):
        db = make_db(templates=[module_template()])
        res = call("POST", f"/api/templates/{TPL}/use", db, user=ASSISTANT)
        assert res.status_code == 403

    def test_broken_tool_template_returns_400(self):
        db = make_db(templates=[tool_template(content={"title": "只有名稱"})])
        before = len(db.rows("ai_tools"))
        res = call("POST", f"/api/templates/{TPL}/use", db)
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "TEMPLATE_INVALID"
        assert len(db.rows("ai_tools")) == before

    def test_missing_template_returns_404(self):
        res = call("POST", f"/api/templates/{TPL}/use", make_db())
        assert res.status_code == 404


class TestDeleteTemplate:

    def test_creator_can_delete(self):
        db = make_db(templates=[tool_template(created_by=ASSISTANT.id)])
        res = call("DELETE", f"/api/templates/{TPL}", db, user=ASSISTANT)
        assert res.status_code == 200
        assert db.rows("templates") == []

    def test_professor_can_delete_any(self):
        db = make_db(templates=[tool_template(created_by=ASSISTANT.id)])
        res = call("DELETE", f"/api/templates/{TPL}", db, user=PROFESSOR)
        assert res.status_code == 200

    def test_other_assistant_cannot_delete(self):
        db = make_db(templates=[tool_template(created_by=ASSISTANT.id)])
        res = call("DELETE", f"/api/templates/{TPL}", db, user=OTHER_ASSISTANT)
        assert res.status_code == 403
        assert len(db.rows("templates")) == 1

    def test_deleting_template_keeps_tools_created_from_it(self):
        db = make_db(templates=[tool_template()])
        created = call("POST", f"/api/templates/{TPL}/use", db).json()["data"]["id"]
        call("DELETE", f"/api/templates/{TPL}", db)
        assert any(t["id"] == created for t in db.rows("ai_tools"))
