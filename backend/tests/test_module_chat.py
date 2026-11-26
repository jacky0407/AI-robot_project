"""
#108 一個模組一個聊天室：api/chat.py（模組聊天室、教師看整段對話）、core/chat_room.py（模組共用機器人、
評分機器人陪聊）、api/modules.py（模組共用機器人設定）、複製模組、機器人版本鎖定

全部 Mock：不呼叫 Gemini，資料庫用記憶體版（tests/inmemory_db.py）。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from api import chat, modules
from core import tool_lock
from core.access import StepAccess
from core.auth import CurrentUser, get_current_user
from core.chat_room import ChatBot, chat_partners, load_bots
from core.templates import create_module, module_content
from tests.inmemory_db import InMemoryDB

STUDENT = CurrentUser(id="student-1", role="student")
PROF = CurrentUser(id="prof-1", role="owner")
SESSION = "5e551011-0000-4000-8000-000000000001"
MOD = "b0000000-0000-4000-8000-000000000001"
S1 = "50000000-0000-4000-8000-000000000001"
S2 = "50000000-0000-4000-8000-000000000002"
S3 = "50000000-0000-4000-8000-000000000003"
EVAL1 = "a0000000-0000-4000-8000-0000000000e1"
EVAL2 = "a0000000-0000-4000-8000-0000000000e2"
EVAL3 = "a0000000-0000-4000-8000-0000000000e3"
PARENT = "a0000000-0000-4000-8000-00000000000a"
TUTOR = "a0000000-0000-4000-8000-00000000000b"

RUBRIC = [{"dimension": "提問品質", "max_score": 100, "description": "能問出關鍵資訊"}]


def tool(tool_id, title, status="published"):
    return {"id": tool_id, "title": title, "status": status, "system_prompt": f"你是{title}",
            "role_instruction": f"歡迎來到{title}", "target_competency": "", "rubric_criteria": RUBRIC,
            "scale_type": "4_point"}


def step(step_id, order, tool_id, title, *, required=True):
    return {"id": step_id, "module_id": MOD, "tool_id": tool_id, "step_order": order, "step_title": title,
            "is_required": required, "pass_score": 70, "require_teacher_review": False, "is_active": True,
            "pass_mode": "score", "step_type": "chat", "ai_tools": tool(tool_id, f"{title}評分")}


def message(mid, step_id, role, content, at, **kw):
    return {"id": mid, "user_id": STUDENT.id, "module_id": MOD, "step_id": step_id, "role": role,
            "content": content, "created_at": at, **kw}


def make_db(*, passed=(), shared=True, stage_bots=True, messages=None):
    bots = []
    if stage_bots:
        bots.append({"id": "rb-parent", "step_id": S1, "module_id": None, "tool_id": PARENT, "display_name": "家長",
                     "when_to_use": "練習和家長溝通", "sort_order": 1, "ai_tools": tool(PARENT, "家長角色")})
    if shared:
        bots.append({"id": "rb-tutor", "step_id": None, "module_id": MOD, "tool_id": TUTOR, "display_name": "學習助教",
                     "when_to_use": "學生問概念", "sort_order": 1, "use_materials": False,
                     "ai_tools": tool(TUTOR, "學習助教")})
    return InMemoryDB(
        learning_modules=[{"id": MOD, "title": "IEP 模組", "is_published": True}],
        module_steps=[step(S1, 1, EVAL1, "家長晤談"), step(S2, 2, EVAL2, "現況描述"),
                      step(S3, 3, EVAL3, "延伸閱讀", required=False)],
        ai_tools=[tool(EVAL1, "家長晤談評分"), tool(EVAL2, "現況描述評分"), tool(EVAL3, "延伸閱讀評分"),
                  tool(PARENT, "家長角色"), tool(TUTOR, "學習助教")],
        chat_room_bots=bots,
        chat_messages=messages or [],
        step_attempts=[{"id": f"att-{s}", "user_id": STUDENT.id, "module_id": MOD, "step_id": s, "attempt_number": 1,
                        "status": "passed", "teacher_reviews": []} for s in passed],
        ai_evaluations=[],
        audit_logs=[],
    )


@pytest.fixture(autouse=True)
def student(monkeypatch):
    main.app.dependency_overrides[get_current_user] = lambda: STUDENT
    monkeypatch.setattr(chat, "check_step_access", lambda db, user, step: StepAccess(mode="course"))
    monkeypatch.setattr(chat, "check_step_unlocked", lambda db, user, step: None)
    monkeypatch.setattr(chat, "ensure_session", lambda db, sid, uid: sid)
    monkeypatch.setattr(chat, "consume_quota", lambda db, access: None)
    monkeypatch.setattr(chat, "refresh_learner_profile", lambda *a, **k: None)
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


def get_room(db, step_id=None, user=STUDENT):
    main.app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(chat, "get_supabase", return_value=db):
        return client.get(f"/api/chat/modules/{MOD}" + (f"?step_id={step_id}" if step_id else ""))


def parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event:
            events.append((event, data))
    return events


def send(db, step_id, content="", evaluate=False, *, reply="好的"):
    client = TestClient(main.app)
    reply_mock = AsyncMock(return_value=reply)
    route_mock = AsyncMock(side_effect=lambda msg, turns, bots: (bots[0], "測試"))
    with patch.object(chat, "get_supabase", return_value=db), \
         patch.object(chat.chat_bot_agent, "reply", reply_mock), \
         patch.object(chat.router_agent, "route", route_mock):
        res = client.post(f"/api/chat/sessions/{SESSION}/steps/{step_id}/messages",
                          json={"content": content, "evaluate": evaluate})
    return res, (parse_sse(res.text) if res.status_code == 200 else []), reply_mock, route_mock


# ── 目前階段 ─────────────────────────────────────────────────────────────────


class TestModuleRoom:

    def test_first_stage_is_current_at_start(self):
        data = get_room(make_db()).json()["data"]
        assert data["current_step_id"] == S1
        assert [s["step_title"] for s in data["stages"]] == ["家長晤談", "現況描述", "延伸閱讀"]
        assert data["stages"][1]["locked"] is True and data["completed"] is False

    def test_moves_to_next_stage_after_passing(self):
        data = get_room(make_db(passed=[S1])).json()["data"]
        assert data["current_step_id"] == S2
        assert data["room"]["evaluator"]["name"] == "現況描述評分"

    def test_completed_when_required_stages_pass(self):
        data = get_room(make_db(passed=[S1, S2])).json()["data"]
        # 選修的第 3 階段沒通過也算完成；目前階段停在還沒通過的那個
        assert data["completed"] is True and data["current_step_id"] == S3

    def test_can_go_back_to_passed_stage(self):
        data = get_room(make_db(passed=[S1]), step_id=S1).json()["data"]
        assert data["current_step_id"] == S1

    def test_cannot_jump_to_locked_stage(self):
        data = get_room(make_db(), step_id=S2).json()["data"]
        assert data["current_step_id"] == S1

    def test_messages_cover_all_stages_in_order(self):
        msgs = [message("m2", S2, "student", "第二階段", "2026-11-26T10:05:00Z"),
                message("m1", S1, "student", "第一階段", "2026-11-26T10:00:00Z")]
        data = get_room(make_db(passed=[S1], messages=msgs)).json()["data"]
        assert [(m["id"], m["step_id"]) for m in data["messages"]] == [("m1", S1), ("m2", S2)]

    def test_stage_intro_comes_from_evaluator_role_instruction(self):
        data = get_room(make_db()).json()["data"]
        assert data["stages"][0]["intro"] == "歡迎來到家長晤談評分"

    def test_room_includes_stage_and_shared_bots(self):
        data = get_room(make_db()).json()["data"]
        assert [b["name"] for b in data["room"]["bots"]] == ["家長", "學習助教"]
        # 第二階段沒有自己的機器人：只有共用的
        data = get_room(make_db(passed=[S1])).json()["data"]
        assert [b["name"] for b in data["room"]["bots"]] == ["學習助教"]

    def test_evaluator_chats_when_stage_has_no_bots(self):
        data = get_room(make_db(passed=[S1], shared=False)).json()["data"]
        assert data["room"]["bots"] == [] and data["room"]["evaluator_chats"] is True

    def test_unpublished_module_is_hidden_from_students(self):
        db = make_db()
        db.tables["learning_modules"][0]["is_published"] = False
        assert get_room(db).status_code == 404


# ── 傳訊息 ───────────────────────────────────────────────────────────────────


class TestMessages:

    def test_bot_sees_earlier_stages(self):
        msgs = [message("m1", S1, "student", "孩子喜歡積木", "2026-11-26T10:00:00Z"),
                message("m2", S1, "bot", "了解", "2026-11-26T10:00:05Z", bot_name="家長", tool_id=PARENT)]
        res, events, reply, _ = send(make_db(passed=[S1], messages=msgs), S2, "現況要怎麼寫")
        assert res.status_code == 200
        turns = reply.await_args.args[2]
        assert [t.content for t in turns][:2] == ["孩子喜歡積木", "了解"]

    def test_message_is_saved_under_current_stage(self):
        db = make_db(passed=[S1])
        send(db, S2, "現況要怎麼寫")
        assert {m["step_id"] for m in db.rows("chat_messages")} == {S2}

    def test_shared_bot_answers_in_stage_without_own_bots(self):
        res, events, _, _ = send(make_db(passed=[S1]), S2, "什麼是現況描述")
        assert [d["bot_name"] for e, d in events if e == "bot_selected"] == ["學習助教"]

    def test_evaluator_replies_when_no_bots(self):
        res, events, reply, _ = send(make_db(passed=[S1], shared=False), S2, "我先試著描述")
        assert [d["bot_name"] for e, d in events if e == "bot_selected"] == ["現況描述評分"]
        assert reply.await_count == 1

    def test_mentioning_evaluator_still_evaluates_when_it_chats(self):
        db = make_db(passed=[S1], shared=False,
                     messages=[message("m1", S2, "student", "孩子能用單字表達需求", "2026-11-26T10:00:00Z")])
        fake = {"type": "score_complete", "data": {"total_score": 80, "percentage": 80, "passed": True,
                                                   "dimension_scores": [], "overall_feedback": "很好"}}

        async def stream(*a, **k):
            yield fake

        with patch.object(chat.practice.tutor_agent._evaluator, "evaluate_stream", stream), \
             patch.object(chat, "extract_and_save", AsyncMock(return_value=None)):
            res, events, reply, _ = send(db, S2, "@現況描述評分 請評分")
        assert any(e == "score_start" for e, _ in events)
        assert reply.await_count == 0

    def test_evaluation_only_uses_current_stage(self):
        msgs = [message("m1", S1, "student", "第一階段的話", "2026-11-26T10:00:00Z"),
                message("m2", S2, "student", "第二階段的話", "2026-11-26T10:05:00Z")]
        db = make_db(passed=[S1], messages=msgs)
        fake = {"type": "score_complete", "data": {"total_score": 60, "percentage": 60, "passed": False,
                                                   "dimension_scores": [], "overall_feedback": "再補充"}}

        async def stream(*a, **k):
            yield fake

        with patch.object(chat.practice.tutor_agent._evaluator, "evaluate_stream", stream):
            send(db, S2, evaluate=True)
        attempt = [a for a in db.rows("step_attempts") if a["step_id"] == S2][0]
        assert "第二階段的話" in attempt["user_input_content"]
        assert "第一階段的話" not in attempt["user_input_content"]


# ── 模組共用機器人 ───────────────────────────────────────────────────────────


class TestSharedBots:

    def test_stage_bot_wins_over_shared_with_same_name(self):
        db = make_db()
        db.tables["chat_room_bots"][1]["display_name"] = "家長"
        assert [b.name for b in load_bots(db, S1, module_id=MOD)] == ["家長"]

    def test_without_module_id_only_stage_bots(self):
        assert [b.name for b in load_bots(make_db(), S1)] == ["家長"]

    def test_missing_module_column_means_no_shared_bots(self):
        db = make_db()

        class Missing(Exception):
            code = "42703"

        real = db.table

        def table(name):
            q = real(name)
            original = q.eq

            def eq(column, value):
                if column == "module_id" and name == "chat_room_bots":
                    raise Missing("column chat_room_bots.module_id does not exist")
                return original(column, value)

            q.eq = eq
            return q

        db.table = table
        assert [b.name for b in load_bots(db, S1, module_id=MOD)] == ["家長"]

    def test_chat_partners_falls_back_to_evaluator_only_when_no_bots(self):
        evaluator = ChatBot(tool_id="e", name="評分", when_to_use="", system_prompt="", role_instruction="")
        other = ChatBot(tool_id="b", name="家長", when_to_use="", system_prompt="", role_instruction="")
        assert chat_partners([], evaluator)[0].name == "評分"
        assert chat_partners([other], evaluator) == [other]
        assert chat_partners([], None) == []


class TestSharedBotSettings:

    def call(self, db, method, body=None, user=PROF):
        main.app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(modules, "get_supabase", return_value=db):
            return client.request(method, f"/api/modules/{MOD}/chat-bots", json=body)

    def test_get_lists_only_shared_bots(self):
        data = self.call(make_db(), "GET").json()["data"]
        assert [b["display_name"] for b in data] == ["學習助教"]

    def test_put_replaces_shared_bots(self):
        db = make_db()
        body = {"bots": [{"tool_id": PARENT, "display_name": "家長代表", "when_to_use": "談家庭作息"}]}
        res = self.call(db, "PUT", body)
        assert res.status_code == 200
        shared = [b for b in db.rows("chat_room_bots") if b.get("module_id") == MOD]
        assert [(b["tool_id"], b["display_name"], b.get("step_id")) for b in shared] == [(PARENT, "家長代表", None)]
        # 階段自己的機器人不動
        assert any(b.get("step_id") == S1 for b in db.rows("chat_room_bots"))
        assert db.rows("audit_logs")[0]["action"] == "module.chat_bots"

    def test_evaluator_cannot_be_shared(self):
        res = self.call(make_db(), "PUT", {"bots": [{"tool_id": EVAL1, "display_name": "評分"}]})
        assert res.status_code == 400 and res.json()["detail"]["code"] == "EVALUATOR_IN_LIST"

    def test_name_cannot_match_any_evaluator(self):
        res = self.call(make_db(), "PUT", {"bots": [{"tool_id": PARENT, "display_name": "現況描述評分"}]})
        assert res.json()["detail"]["code"] == "DUPLICATE_NAME"

    def test_assistant_cannot_change_shared_bots(self):
        res = self.call(make_db(), "PUT", {"bots": []}, user=CurrentUser(id="ta", role="assistant"))
        assert res.status_code == 403


# ── 教師看整段對話 ───────────────────────────────────────────────────────────


class TestStaffTranscript:

    def call(self, db, user):
        main.app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(chat, "get_supabase", return_value=db):
            return client.get(f"/api/chat/modules/{MOD}/users/{STUDENT.id}")

    def test_teacher_sees_whole_module_with_stage_labels(self):
        msgs = [message("m1", S1, "student", "一", "2026-11-26T10:00:00Z"),
                message("m2", S2, "student", "二", "2026-11-26T10:05:00Z")]
        data = self.call(make_db(passed=[S1], messages=msgs), PROF).json()["data"]
        assert [m["stage"] for m in data["messages"]] == ["階段 1：家長晤談", "階段 2：現況描述"]
        assert data["stages"][0]["passed"] is True

    def test_students_cannot_read_others(self):
        assert self.call(make_db(), STUDENT).status_code == 403


# ── 複製模組、版本鎖定 ───────────────────────────────────────────────────────


class TestCopyAndLock:

    def test_module_content_keeps_shared_bots(self):
        db = make_db()
        content = module_content({"title": "IEP 模組"}, db.rows("module_steps"), db.rows("chat_room_bots"))
        assert [b["display_name"] for b in content["chat_bots"]] == ["學習助教"]
        assert [b["display_name"] for b in content["steps"][0]["chat_bots"]] == ["家長"]

    def test_copy_creates_shared_bots(self):
        db = make_db()
        content = module_content({"title": "IEP 模組"}, db.rows("module_steps"), db.rows("chat_room_bots"))
        new_module, skipped = create_module(db, content, title="副本")
        shared = [b for b in db.rows("chat_room_bots") if b.get("module_id") == new_module["id"]]
        assert skipped == [] and [b["display_name"] for b in shared] == ["學習助教"]

    def test_tool_usages_include_shared_bot(self):
        usages = tool_lock.load_tool_usages(make_db(), TUTOR)
        assert usages == [{"kind": "chat_bot", "id": "rb-tutor", "module_id": MOD, "step_id": None,
                           "step_title": "整個模組共用", "tool_version": None}]
