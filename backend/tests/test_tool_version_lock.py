"""
單元測試：模組停在機器人的舊版本（#89 後半，core/tool_lock.py、api/teacher.py，migration 024）

用記憶體版 Supabase（tests/inmemory_db.py），不連線、不寫入真的資料庫。

重點：
  - 停在舊版的關卡：教學內容換成那一版，名稱與狀態用最新的，version 是那一版
  - 修改機器人時逐個模組選：選了的更新成新版、沒選的停在修改前那一版
  - 編輯頁看得到哪些模組在用、各自停在哪一版
  - 換機器人、整份換掉聊天室機器人、複製模組時，「停在第幾版」的處理
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import modules, teacher
from core import tool_lock
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB

PROFESSOR = CurrentUser(id="prof-1", role="owner")

TOOL = "a0000000-0000-4000-8000-00000000000a"
OTHER_TOOL = "a0000000-0000-4000-8000-00000000000b"
MOD_A = "b0000000-0000-4000-8000-00000000000a"
MOD_B = "b0000000-0000-4000-8000-00000000000b"
MOD_C = "b0000000-0000-4000-8000-00000000000c"
STEP_A = "50000000-0000-4000-8000-00000000000a"
STEP_B = "50000000-0000-4000-8000-00000000000b"
STEP_C = "50000000-0000-4000-8000-00000000000c"

OLD_RUBRIC = [{"dimension": "舊構面", "max_score": 100, "description": "第 1 版的規準"}]
NEW_RUBRIC = [{"dimension": "新構面", "max_score": 100, "description": "第 2 版的規準"}]


def tool_v(version: int, **overrides) -> dict:
    """某一版的機器人內容"""
    return {
        "id": TOOL,
        "title": f"教練 v{version}",
        "domain": "學前IEP",
        "target_competency": "現況描述",
        "role_instruction": f"角色 v{version}",
        "system_prompt": f"指令 v{version}",
        "teaching_strategy": {"hints": [{"level": 1, "content": f"提示 v{version}"}]},
        "rubric_criteria": OLD_RUBRIC if version == 1 else NEW_RUBRIC,
        "scale_type": "4_point",
        "error_taxonomy": [],
        "status": "published",
        "version": version,
        "created_by": PROFESSOR.id,
        **overrides,
    }


def step(step_id, module_id, title, tool_version=None, tool_id=TOOL, **extra) -> dict:
    return {"id": step_id, "module_id": module_id, "tool_id": tool_id, "step_order": 1, "step_title": title,
            "is_required": True, "pass_score": 70, "require_teacher_review": False, "is_active": True,
            "pass_mode": "score", "step_type": "answer", "tool_version": tool_version, **extra}


def make_db(**extra) -> InMemoryDB:
    tables = dict(
        ai_tools=[tool_v(2), {**tool_v(1), "id": OTHER_TOOL, "title": "另一支", "version": 1}],
        ai_tool_versions=[
            {"id": "v1", "tool_id": TOOL, "version": 1, "snapshot": tool_v(1)},
            {"id": "v2", "tool_id": TOOL, "version": 2, "snapshot": tool_v(2)},
        ],
        learning_modules=[
            {"id": MOD_A, "title": "A 模組", "is_published": True},
            {"id": MOD_B, "title": "B 模組", "is_published": True},
            {"id": MOD_C, "title": "C 模組", "is_published": False},
        ],
        module_steps=[
            step(STEP_A, MOD_A, "A 的關卡"),
            step(STEP_B, MOD_B, "B 的關卡"),
            step(STEP_C, MOD_C, "C 的聊天室", tool_id=OTHER_TOOL, step_type="chat"),
        ],
        chat_room_bots=[
            {"id": "bot-c", "step_id": STEP_C, "tool_id": TOOL, "display_name": "教練", "when_to_use": "",
             "sort_order": 1, "tool_version": None},
        ],
        step_attempts=[],
        course_modules=[],
    )
    tables.update(extra)
    return InMemoryDB(**tables)


@pytest.fixture(autouse=True)
def reset_login():
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def call(method, path, db, json=None, user=PROFESSOR):
    main.app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(teacher, "get_supabase", return_value=db), patch.object(modules, "get_supabase", return_value=db):
        return client.request(method, path, json=json)


def row(db, table, row_id):
    return next(r for r in db.rows(table) if r["id"] == row_id)


# ── 套用舊版內容 ────────────────────────────────────────────────────────────


class TestApplyLockedVersion:

    def test_unlocked_step_keeps_latest(self):
        db = make_db()
        s = {**step(STEP_A, MOD_A, "x"), "ai_tools": tool_v(2)}
        tool_lock.apply_locked_version(db, s)
        assert s["ai_tools"]["rubric_criteria"] == NEW_RUBRIC
        assert db.calls == []  # 跟著最新版：不用查舊版

    def test_locked_step_uses_old_content(self):
        db = make_db()
        s = {**step(STEP_A, MOD_A, "x", tool_version=1), "ai_tools": tool_v(2)}
        tool_lock.apply_locked_version(db, s)
        t = s["ai_tools"]
        assert t["rubric_criteria"] == OLD_RUBRIC
        assert t["system_prompt"] == "指令 v1"
        assert t["teaching_strategy"]["hints"][0]["content"] == "提示 v1"
        assert t["version"] == 1  # 評分記下的版本才會是第 1 版

    def test_locked_step_keeps_live_title_and_status(self):
        """名稱（聊天室 @ 用）與封存狀態以最新的為準"""
        db = make_db()
        s = {**step(STEP_A, MOD_A, "x", tool_version=1), "ai_tools": {**tool_v(2), "status": "archived"}}
        tool_lock.apply_locked_version(db, s)
        assert s["ai_tools"]["title"] == "教練 v2"
        assert s["ai_tools"]["status"] == "archived"

    def test_lock_equal_to_latest_needs_no_lookup(self):
        db = make_db()
        s = {**step(STEP_A, MOD_A, "x", tool_version=2), "ai_tools": tool_v(2)}
        tool_lock.apply_locked_version(db, s)
        assert db.calls == []

    def test_missing_snapshot_falls_back_to_latest(self):
        db = make_db(ai_tool_versions=[])
        s = {**step(STEP_A, MOD_A, "x", tool_version=1), "ai_tools": tool_v(2)}
        tool_lock.apply_locked_version(db, s)
        assert s["ai_tools"]["rubric_criteria"] == NEW_RUBRIC

    def test_snapshot_read_error_falls_back_to_latest(self):
        db = make_db()
        db.errors[("ai_tool_versions", "select")] = RuntimeError("db down")
        s = {**step(STEP_A, MOD_A, "x", tool_version=1), "ai_tools": tool_v(2)}
        tool_lock.apply_locked_version(db, s)
        assert s["ai_tools"]["version"] == 2

    def test_partial_tool_select_without_version_still_locks(self):
        """review.py 只帶 ai_tools(title, rubric_criteria)，沒有 version 也要換成舊版"""
        db = make_db()
        s = {**step(STEP_A, MOD_A, "x", tool_version=1), "ai_tools": {"title": "教練 v2", "rubric_criteria": NEW_RUBRIC}}
        tool_lock.apply_locked_version(db, s)
        assert s["ai_tools"]["rubric_criteria"] == OLD_RUBRIC

    def test_cache_avoids_repeated_lookups(self):
        db = make_db()
        cache: dict = {}
        for _ in range(3):
            s = {**step(STEP_A, MOD_A, "x", tool_version=1), "ai_tools": tool_v(2)}
            tool_lock.apply_locked_version(db, s, cache=cache)
        assert sum(1 for c in db.calls if c[0] == "ai_tool_versions") == 1

    def test_locked_version_ignores_invalid_values(self):
        assert tool_lock.locked_version({"tool_version": None}) is None
        assert tool_lock.locked_version({"tool_version": 0}) is None
        assert tool_lock.locked_version({}) is None
        assert tool_lock.locked_version({"tool_version": 3}) == 3


# ── 修改時逐個模組選 ──────────────────────────────────────────────────────────


class TestPlanLockChanges:

    USAGES = [
        {"kind": "step", "id": "s-a", "module_id": "A", "tool_version": None},
        {"kind": "step", "id": "s-b", "module_id": "B", "tool_version": None},
        {"kind": "chat_bot", "id": "bot-c", "module_id": "C", "tool_version": 1},
    ]

    def test_unselected_modules_stay_on_current_version(self):
        changes = tool_lock.plan_lock_changes(self.USAGES, {"A"}, current_version=2)
        assert {"kind": "step", "id": "s-b", "tool_version": 2} in changes
        assert all(c["id"] != "s-a" for c in changes)

    def test_selected_locked_module_goes_back_to_latest(self):
        changes = tool_lock.plan_lock_changes(self.USAGES, {"C"}, current_version=2)
        assert {"kind": "chat_bot", "id": "bot-c", "tool_version": None} in changes

    def test_unselected_module_already_on_older_version_is_untouched(self):
        changes = tool_lock.plan_lock_changes(self.USAGES, {"A", "B"}, current_version=2)
        assert changes == []

    def test_select_all_changes_nothing_for_following_modules(self):
        usages = self.USAGES[:2]
        assert tool_lock.plan_lock_changes(usages, {"A", "B"}, current_version=5) == []


class TestEditPageUsage:

    def test_get_tool_lists_modules_using_it(self):
        db = make_db()
        db.tables["chat_room_bots"][0]["tool_version"] = 1
        res = call("GET", f"/api/teacher/tools/{TOOL}", db)
        used = {m["title"]: m for m in res.json()["data"]["used_by"]}
        assert set(used) == {"A 模組", "B 模組", "C 模組"}
        assert used["A 模組"]["follows_latest"] is True
        assert used["C 模組"]["follows_latest"] is False
        assert used["C 模組"]["locked_versions"] == [1]
        assert used["C 模組"]["steps"][0]["kind"] == "chat_bot"

    def test_unused_tool_has_empty_used_by(self):
        db = make_db(module_steps=[], chat_room_bots=[])
        res = call("GET", f"/api/teacher/tools/{TOOL}", db)
        assert res.json()["data"]["used_by"] == []

    def test_without_chat_tables_still_lists_steps(self):
        db = make_db()
        db.errors[("chat_room_bots", "select")] = APIError({"code": "42P01", "message": "relation does not exist"})
        res = call("GET", f"/api/teacher/tools/{TOOL}", db)
        assert res.status_code == 200
        assert {m["title"] for m in res.json()["data"]["used_by"]} == {"A 模組", "B 模組"}


class TestUpdateWithModuleChoice:

    def patch_tool(self, db, update_modules, **body):
        return call("PATCH", f"/api/teacher/tools/{TOOL}", db,
                    json={"system_prompt": "指令 v3", "rubric_criteria": NEW_RUBRIC + [
                        {"dimension": "第三版新增", "max_score": 10, "description": "x"}],
                          "update_modules": update_modules, **body})

    def test_selected_module_updates_others_stay_on_old_version(self):
        db = make_db()
        res = self.patch_tool(db, [MOD_A])
        assert res.status_code == 200
        assert res.json()["data"]["version"] == 3
        assert row(db, "module_steps", STEP_A)["tool_version"] is None      # A：用新版
        assert row(db, "module_steps", STEP_B)["tool_version"] == 2         # B：停在修改前的第 2 版
        assert row(db, "chat_room_bots", "bot-c")["tool_version"] == 2      # C 的聊天室機器人也停在第 2 版

    def test_response_shows_new_usage(self):
        db = make_db()
        used = {m["title"]: m for m in self.patch_tool(db, [MOD_A]).json()["used_by"]}
        assert used["A 模組"]["follows_latest"] is True
        assert used["B 模組"]["locked_versions"] == [2]

    def test_previously_locked_module_can_be_updated_later(self):
        db = make_db()
        db.tables["module_steps"][1]["tool_version"] = 1  # B 先前停在第 1 版
        self.patch_tool(db, [MOD_A, MOD_B, MOD_C])
        assert row(db, "module_steps", STEP_B)["tool_version"] is None

    def test_old_version_content_still_available_for_locked_module(self):
        """B 停在第 2 版：第 2 版的內容在 ai_tool_versions 裡，學生作答會用它"""
        db = make_db()
        self.patch_tool(db, [MOD_A])
        s = {**row(db, "module_steps", STEP_B), "ai_tools": row(db, "ai_tools", TOOL)}
        tool_lock.apply_locked_version(db, s)
        assert s["ai_tools"]["system_prompt"] == "指令 v2"
        assert s["ai_tools"]["version"] == 2

    def test_without_choice_keeps_old_behaviour(self):
        db = make_db()
        res = call("PATCH", f"/api/teacher/tools/{TOOL}", db, json={"system_prompt": "指令 v3"})
        assert res.status_code == 200
        assert [s["tool_version"] for s in db.rows("module_steps")] == [None, None, None]

    def test_no_content_change_without_choice_touches_nothing(self):
        db = make_db()
        db.tables["module_steps"][1]["tool_version"] = 1
        res = call("PATCH", f"/api/teacher/tools/{TOOL}", db, json={"system_prompt": "指令 v2"})
        assert res.json()["changed"] is False
        assert row(db, "module_steps", STEP_B)["tool_version"] == 1

    def test_no_content_change_can_move_locked_module_to_latest(self):
        """內容沒改，只是把先前停在舊版的 B 改回跟著最新版；不升版"""
        db = make_db()
        db.tables["module_steps"][1]["tool_version"] = 1
        res = call("PATCH", f"/api/teacher/tools/{TOOL}", db,
                   json={"system_prompt": "指令 v2", "update_modules": [MOD_A, MOD_B, MOD_C]})
        body = res.json()
        assert body["changed"] is False
        assert row(db, "ai_tools", TOOL)["version"] == 2
        assert row(db, "module_steps", STEP_B)["tool_version"] is None
        assert {m["title"]: m for m in body["used_by"]}["B 模組"]["follows_latest"] is True

    def test_stale_expected_version_changes_nothing(self):
        db = make_db()
        res = self.patch_tool(db, [], expected_version=1)
        assert res.status_code == 409
        assert row(db, "module_steps", STEP_B)["tool_version"] is None

    def test_missing_migration_returns_503_and_tool_unchanged(self):
        db = make_db()
        db.errors[("module_steps", "update")] = APIError({"code": "42703", "message": "column tool_version does not exist"})
        res = self.patch_tool(db, [MOD_A])
        assert res.status_code == 503
        assert "024_tool_version_lock.sql" in res.json()["detail"]["message"]
        assert row(db, "ai_tools", TOOL)["version"] == 2

    def test_unknown_module_ids_are_ignored(self):
        db = make_db()
        res = self.patch_tool(db, [MOD_A, MOD_B, MOD_C, "not-a-module"])
        assert res.status_code == 200


# ── 模組管理 ────────────────────────────────────────────────────────────────


class TestModuleManagement:

    def test_module_detail_shows_locked_version(self):
        db = make_db()
        db.tables["module_steps"][1]["tool_version"] = 1
        res = call("GET", f"/api/modules/{MOD_B}", db)
        assert res.json()["data"]["steps"][0]["tool_version"] == 1

    def test_switching_tool_clears_lock(self):
        db = make_db()
        db.tables["module_steps"][1]["tool_version"] = 1
        res = call("PATCH", f"/api/modules/{MOD_B}/steps/{STEP_B}", db, json={"tool_id": OTHER_TOOL})
        assert res.status_code == 200
        assert row(db, "module_steps", STEP_B)["tool_version"] is None

    def test_replacing_chat_bots_keeps_their_lock(self):
        db = make_db()
        db.tables["chat_room_bots"][0]["tool_version"] = 1
        res = call("PUT", f"/api/modules/{MOD_C}/steps/{STEP_C}/chat-bots", db,
                   json={"bots": [{"tool_id": TOOL, "display_name": "教練（改名）", "when_to_use": "新的時機"}]})
        assert res.status_code == 200
        bots = db.rows("chat_room_bots")
        assert len(bots) == 1 and bots[0]["tool_version"] == 1

    def test_copied_module_keeps_lock(self):
        db = make_db()
        db.tables["module_steps"][1]["tool_version"] = 1
        res = call("POST", f"/api/modules/{MOD_B}/copy", db)
        new_id = res.json()["data"]["id"]
        copied = next(s for s in db.rows("module_steps") if s["module_id"] == new_id)
        assert copied["tool_version"] == 1
