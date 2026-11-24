"""
#68 多機器人聊天室：api/chat.py、core/chat_room.py、core/ai/agents/chat_agents.py、模組管理的聊天室設定

全部 Mock：不呼叫 Gemini（Agent 一律換成 AsyncMock 或假的 LLM），資料庫用記憶體版（tests/inmemory_db.py）。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import main
from api import chat, modules, practice
from core.access import StepAccess
from core.ai.agents.chat_agents import ChatBot, ChatTurn, RouterAgent
from core.auth import CurrentUser, get_current_user
from core.chat_room import find_mention, transcript_for_evaluation
from tests.inmemory_db import InMemoryDB

STUDENT = CurrentUser(id="student-1", role="student")
PROFESSOR = CurrentUser(id="prof-1", role="owner")
SESSION = "5e551011-0000-4000-8000-000000000001"
MOD = "b0000000-0000-4000-8000-000000000001"
STEP = "50000000-0000-4000-8000-000000000001"
EVAL_TOOL = "a0000000-0000-4000-8000-0000000000e1"
BOT_A = "a0000000-0000-4000-8000-00000000000a"
BOT_B = "a0000000-0000-4000-8000-00000000000b"
BOT_DRAFT = "a0000000-0000-4000-8000-0000000000dd"

RUBRIC = [{"dimension": "提問品質", "max_score": 50, "description": "能問出關鍵資訊"},
          {"dimension": "專業用語", "max_score": 50, "description": "用語正確"}]


def tool(tool_id, title, status="published", rubric=None):
    return {"id": tool_id, "title": title, "status": status, "system_prompt": f"你是{title}",
            "role_instruction": "", "rubric_criteria": rubric or [], "scale_type": "4_point"}


def make_db(*, step_type="chat", rubric=RUBRIC, pass_mode="score", bots=("a", "b"), messages=None):
    bot_rows = []
    if "a" in bots:
        bot_rows.append({"id": "rb-a", "step_id": STEP, "tool_id": BOT_A, "display_name": "家長",
                         "when_to_use": "學生要練習和家長溝通", "sort_order": 1, "ai_tools": tool(BOT_A, "家長角色")})
    if "b" in bots:
        bot_rows.append({"id": "rb-b", "step_id": STEP, "tool_id": BOT_B, "display_name": "IEP 小幫手",
                         "when_to_use": "學生問 IEP 怎麼寫", "sort_order": 2, "ai_tools": tool(BOT_B, "IEP 小幫手")})
    if "draft" in bots:
        bot_rows.append({"id": "rb-d", "step_id": STEP, "tool_id": BOT_DRAFT, "display_name": "草稿",
                         "when_to_use": "", "sort_order": 3, "ai_tools": tool(BOT_DRAFT, "草稿", status="draft")})
    return InMemoryDB(
        learning_modules=[{"id": MOD, "title": "模組", "description": None, "domain": "學前IEP",
                           "is_published": True, "created_at": "2026-11-01"}],
        module_steps=[{"id": STEP, "module_id": MOD, "tool_id": EVAL_TOOL, "step_order": 1, "step_title": "家長晤談",
                       "is_required": True, "pass_score": 70, "require_teacher_review": False, "is_active": True,
                       "pass_mode": pass_mode, "step_type": step_type,
                       "ai_tools": tool(EVAL_TOOL, "評分老師", rubric=rubric)}],
        ai_tools=[tool(EVAL_TOOL, "評分老師", rubric=rubric), tool(BOT_A, "家長角色"), tool(BOT_B, "IEP 小幫手"),
                  tool(BOT_DRAFT, "草稿", status="draft")],
        chat_room_bots=bot_rows,
        chat_messages=messages or [],
        step_attempts=[],
        ai_evaluations=[],
    )


@pytest.fixture(autouse=True)
def student(monkeypatch):
    main.app.dependency_overrides[get_current_user] = lambda: STUDENT
    quota_calls = []
    monkeypatch.setattr(chat, "check_step_access", lambda db, user, step: StepAccess(mode="course"))
    monkeypatch.setattr(chat, "check_step_unlocked", lambda db, user, step: None)
    monkeypatch.setattr(chat, "ensure_session", lambda db, sid, uid: sid)
    monkeypatch.setattr(chat, "consume_quota", lambda db, access: quota_calls.append(1) or None)
    monkeypatch.setattr(chat, "refresh_learner_profile", lambda *a, **k: None)
    yield quota_calls
    main.app.dependency_overrides.pop(get_current_user, None)


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


def send(db, content="", evaluate=False, *, reply="好的，我們來談談。"):
    client = TestClient(main.app)
    reply_mock = AsyncMock(return_value=reply)
    with patch.object(chat, "get_supabase", return_value=db), \
         patch.object(chat.chat_bot_agent, "reply", reply_mock):
        res = client.post(f"/api/chat/sessions/{SESSION}/steps/{STEP}/messages",
                          json={"content": content, "evaluate": evaluate})
    return res, (parse_sse(res.text) if res.status_code == 200 else []), reply_mock


def events_named(events, name):
    return [d for e, d in events if e == name]


# ──────────────────────────────────────────
# 讀取聊天室
# ──────────────────────────────────────────

class TestGetRoom:

    def get(self, db):
        client = TestClient(main.app)
        with patch.object(chat, "get_supabase", return_value=db):
            return client.get(f"/api/chat/steps/{STEP}")

    def test_answer_step_is_not_a_chat_room(self):
        res = self.get(make_db(step_type="answer"))
        assert res.status_code == 200
        assert res.json()["data"] == {"chat": False}

    def test_room_lists_published_bots_evaluator_and_own_messages(self):
        db = make_db(bots=("a", "b", "draft"), messages=[
            {"id": "m1", "user_id": "student-1", "step_id": STEP, "role": "student", "content": "你好"},
            {"id": "m2", "user_id": "student-2", "step_id": STEP, "role": "student", "content": "別人的"},
        ])
        data = self.get(db).json()["data"]

        assert data["chat"] is True
        assert [b["name"] for b in data["bots"]] == ["家長", "IEP 小幫手"]   # 草稿機器人不出現
        assert data["evaluator"]["name"] == "評分老師"
        assert [m["content"] for m in data["messages"]] == ["你好"]        # 看不到別人的對話
        assert data["scoring"] is True and data["pass_mode"] == "score"


# ──────────────────────────────────────────
# 傳訊息：一則只由一個機器人回答
# ──────────────────────────────────────────

class TestSendMessage:

    def test_mention_picks_that_bot_without_router(self, student):
        db = make_db()
        route = AsyncMock()
        with patch.object(chat.router_agent, "route", route):
            res, events, reply = send(db, "@IEP 小幫手 長期目標怎麼寫？")

        assert res.status_code == 200
        selected = events_named(events, "bot_selected")[0]
        assert selected["bot_name"] == "IEP 小幫手" and selected["by"] == "mention"
        route.assert_not_awaited()
        assert reply.await_args.args[0].name == "IEP 小幫手"
        saved = db.rows("chat_messages")
        assert [(m["role"], m.get("bot_name")) for m in saved] == [("student", None), ("bot", "IEP 小幫手")]
        assert events_named(events, "bot_reply")[0]["message"]["content"] == "好的，我們來談談。"
        assert len(student) == 1   # 每則訊息扣一次

    def test_without_mention_router_chooses_one_bot(self):
        db = make_db()
        with patch.object(chat.router_agent, "route", AsyncMock(return_value=(
            ChatBot(tool_id=BOT_A, name="家長"), "學生在練習和家長溝通"))):
            _, events, reply = send(db, "我想跟家長說明孩子的進步")

        selected = events_named(events, "bot_selected")[0]
        assert selected == {"tool_id": BOT_A, "bot_name": "家長", "by": "router", "reason": "學生在練習和家長溝通"}
        assert reply.await_count == 1
        bot_msg = db.rows("chat_messages")[-1]
        assert bot_msg["routing"]["by"] == "router"

    def test_single_bot_room_skips_router(self):
        db = make_db(bots=("a",))
        route = AsyncMock()
        with patch.object(chat.router_agent, "route", route):
            _, events, _ = send(db, "你好")

        assert events_named(events, "bot_selected")[0]["by"] == "only_bot"
        route.assert_not_awaited()

    def test_pii_is_blocked_before_saving_or_charging(self, student):
        db = make_db()
        res, _, reply = send(db, "孩子身分證 A123456789")

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "PII_DETECTED"
        assert db.rows("chat_messages") == []
        assert student == []
        reply.assert_not_awaited()

    def test_answer_step_rejects_chat_messages(self):
        res, _, _ = send(make_db(step_type="answer"), "你好")
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "NOT_CHAT_STEP"

    def test_empty_message_is_rejected(self):
        res, _, _ = send(make_db(), "   ")
        assert res.status_code == 400

    def test_room_without_chat_bots_rejects_normal_messages(self):
        res, _, _ = send(make_db(bots=()), "你好")
        assert res.json()["detail"]["code"] == "NO_CHAT_BOTS"

    def test_bot_failure_reports_error_and_refunds(self, monkeypatch):
        db = make_db(bots=("a",))
        monkeypatch.setattr(chat, "consume_quota", lambda db, access: {"used": 1, "quota": 5})
        refunds = []
        monkeypatch.setattr(chat, "refund_quota", lambda db, access: refunds.append(1) or {"used": 0, "quota": 5})
        client = TestClient(main.app)
        with patch.object(chat, "get_supabase", return_value=db), \
             patch.object(chat.chat_bot_agent, "reply", AsyncMock(side_effect=RuntimeError("timeout"))):
            res = client.post(f"/api/chat/sessions/{SESSION}/steps/{STEP}/messages", json={"content": "你好"})
        names = [e for e, _ in parse_sse(res.text)]

        assert "error" in names and "quota_refunded" in names
        assert refunds == [1]


# ──────────────────────────────────────────
# 評分機器人
# ──────────────────────────────────────────

def dim(name, score):
    return json.dumps({"dimension": name, "score": score, "max_score": 4, "reason": "r", "evidence": "e"})


def mock_evaluator(responses):
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=[AIMessage(content=r) for r in responses])
    practice.tutor_agent._evaluator._llm = llm


HISTORY = [
    {"id": "m1", "user_id": "student-1", "step_id": STEP, "role": "student", "content": "請問孩子在家的作息？"},
    {"id": "m2", "user_id": "student-1", "step_id": STEP, "role": "bot", "bot_name": "家長", "content": "他早上七點起床。"},
]


class TestEvaluation:

    def test_evaluate_button_scores_whole_conversation(self):
        db = make_db(messages=list(HISTORY))
        mock_evaluator([dim("提問品質", 4), dim("專業用語", 3),
                        json.dumps({"overall_feedback": "提問具體", "confidence": 0.9})])
        _, events, reply = send(db, evaluate=True)

        reply.assert_not_awaited()                       # 評分不是對話機器人回答
        attempt = db.rows("step_attempts")[0]
        assert "學生：請問孩子在家的作息？" in attempt["user_input_content"]
        assert "家長：他早上七點起床。" in attempt["user_input_content"]
        assert attempt["structured_data"]["source"] == "chat"
        assert attempt["status"] == "passed"
        evaluation = db.rows("ai_evaluations")[0]
        assert evaluation["attempt_id"] == attempt["id"] and evaluation["feedback_text"] == "提問具體"
        assert events_named(events, "pass_status")[0]["passed"] is True
        last = db.rows("chat_messages")[-1]
        assert last["bot_name"] == "評分老師" and last["attempt_id"] == attempt["id"]

    def test_mentioning_evaluator_triggers_evaluation(self):
        db = make_db(messages=list(HISTORY))
        mock_evaluator([dim("提問品質", 1), dim("專業用語", 1),
                        json.dumps({"overall_feedback": "再多問一些", "confidence": 0.9})])
        _, events, _ = send(db, "@評分老師 我問完了")

        assert events_named(events, "bot_selected")[0]["by"] == "evaluate"
        assert db.rows("step_attempts")[0]["status"] == "revision_required"
        # 學生這則 @ 訊息也算在對話裡
        assert "我問完了" in db.rows("step_attempts")[0]["user_input_content"]

    def test_evaluator_without_rubric_gives_feedback_only(self):
        db = make_db(rubric=[], messages=list(HISTORY))
        with patch.object(practice.feedback_agent, "feedback", AsyncMock(return_value="繼續加油")):
            _, events, _ = send(db, evaluate=True)

        assert db.rows("ai_evaluations") == []           # 不寫假分數
        assert events_named(events, "feedback_complete")[0]["feedback"] == "繼續加油"
        assert db.rows("step_attempts")[0]["status"] == "passed"   # 沒有規準 → 作答即通過

    def test_teacher_review_mode_waits_for_teacher(self):
        db = make_db(pass_mode="teacher_review", messages=list(HISTORY))
        mock_evaluator([dim("提問品質", 4), dim("專業用語", 4),
                        json.dumps({"overall_feedback": "很好", "confidence": 0.9})])
        _, events, _ = send(db, evaluate=True)

        assert db.rows("step_attempts")[0]["status"] == "submitted"
        assert events_named(events, "pass_status")[0]["awaiting_teacher"] is True

    def test_nothing_to_evaluate(self):
        db = make_db()
        _, events, _ = send(db, evaluate=True)

        assert events_named(events, "error")
        assert db.rows("step_attempts") == []


# ──────────────────────────────────────────
# 規則本身
# ──────────────────────────────────────────

class TestRules:

    BOTS = [ChatBot(tool_id="1", name="IEP"), ChatBot(tool_id="2", name="IEP 小幫手"), ChatBot(tool_id="3", name="家長")]

    def test_mention_prefers_longest_name(self):
        assert find_mention("@IEP 小幫手 你好", self.BOTS).tool_id == "2"
        assert find_mention("@IEP 你好", self.BOTS).tool_id == "1"

    def test_fullwidth_at_sign_works(self):
        assert find_mention("＠家長 早安", self.BOTS).tool_id == "3"

    def test_unknown_mention_returns_none(self):
        assert find_mention("@老師 在嗎", self.BOTS) is None
        assert find_mention("沒有指名", self.BOTS) is None

    def test_transcript_keeps_latest_and_skips_evaluation_results(self):
        rows = [{"role": "student", "content": "舊" * 50}, {"role": "student", "content": "新的問題"},
                {"role": "bot", "bot_name": "評分老師", "content": "評分：80 分", "routing": {"by": "evaluate"}}]
        text = transcript_for_evaluation(rows, limit=20)
        assert text == "學生：新的問題"

    @pytest.mark.asyncio
    async def test_router_falls_back_to_first_bot_on_bad_answer(self):
        agent = RouterAgent()
        with patch("core.ai.agents.chat_agents.invoke_json", AsyncMock(return_value={"bot": 9, "reason": "?"})), \
             patch("core.ai.agents.chat_agents.chat_llm", lambda t, **kw: object()):
            bot, _ = await agent.route("你好", [ChatTurn(role="student", content="嗨")], self.BOTS)
        assert bot.tool_id == "1"

    @pytest.mark.asyncio
    async def test_router_uses_llm_choice(self):
        agent = RouterAgent()
        with patch("core.ai.agents.chat_agents.invoke_json", AsyncMock(return_value={"bot": 3, "reason": "家長情境"})), \
             patch("core.ai.agents.chat_agents.chat_llm", lambda t, **kw: object()):
            bot, reason = await agent.route("想和家長談", [], self.BOTS)
        assert bot.tool_id == "3" and reason == "家長情境"


# ──────────────────────────────────────────
# 教師端：設定聊天室的機器人
# ──────────────────────────────────────────

class TestChatBotsConfig:

    @pytest.fixture(autouse=True)
    def professor(self, student):
        main.app.dependency_overrides[get_current_user] = lambda: PROFESSOR
        yield

    def call(self, method, path, db, json=None):
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(modules, "get_supabase", return_value=db):
            return client.request(method, f"/api/modules/{MOD}{path}", json=json)

    def test_replace_bots(self):
        db = make_db()
        res = self.call("PUT", f"/steps/{STEP}/chat-bots", db, json={"bots": [
            {"tool_id": BOT_B, "display_name": "小幫手", "when_to_use": "問 IEP"},
        ]})

        assert res.status_code == 200
        rows = db.rows("chat_room_bots")
        assert [(r["tool_id"], r["display_name"], r["sort_order"]) for r in rows] == [(BOT_B, "小幫手", 1)]

    @pytest.mark.parametrize("bots, code", [
        ([{"tool_id": EVAL_TOOL, "display_name": "x"}], "EVALUATOR_IN_LIST"),
        ([{"tool_id": BOT_A, "display_name": "家長"}, {"tool_id": BOT_B, "display_name": "家長"}], "DUPLICATE_NAME"),
        ([{"tool_id": BOT_A, "display_name": "評分老師"}], "DUPLICATE_NAME"),   # 和評分機器人同名，@ 會分不清
        ([{"tool_id": BOT_A, "display_name": "a"}, {"tool_id": BOT_A, "display_name": "b"}], "DUPLICATE_TOOL"),
    ])
    def test_invalid_bots_are_rejected(self, bots, code):
        db = make_db()
        res = self.call("PUT", f"/steps/{STEP}/chat-bots", db, json={"bots": bots})

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == code
        assert len(db.rows("chat_room_bots")) == 2   # 沒有被清掉

    def test_assistant_cannot_change_bots(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="as-1", role="assistant")
        res = self.call("PUT", f"/steps/{STEP}/chat-bots", make_db(), json={"bots": []})
        assert res.status_code == 403

    def test_create_chat_step(self):
        db = make_db()
        res = self.call("POST", "/steps", db, json={"tool_id": EVAL_TOOL, "step_title": "聊天室", "step_type": "chat"})

        assert res.status_code == 201
        assert res.json()["data"]["steps"][-1]["step_type"] == "chat"


# ──────────────────────────────────────────
# 停在機器人舊版（#89 後半，migration 024）
# ──────────────────────────────────────────

class TestLockedVersion:

    OLD_RUBRIC = [{"dimension": "舊版構面", "max_score": 100, "description": "第 1 版"}]

    def locked_db(self):
        db = make_db(messages=list(HISTORY))
        step = db.tables["module_steps"][0]
        step["tool_version"] = 1
        step["ai_tools"]["version"] = 2
        db.tables["ai_tool_versions"] = [{
            "id": "v1", "tool_id": EVAL_TOOL, "version": 1,
            "snapshot": {**tool(EVAL_TOOL, "評分老師（舊名）", rubric=self.OLD_RUBRIC), "version": 1},
        }]
        return db

    def test_locked_evaluator_scores_with_old_rubric(self):
        db = self.locked_db()
        mock_evaluator([dim("舊版構面", 4), json.dumps({"overall_feedback": "好", "confidence": 0.9})])
        send(db, evaluate=True)

        evaluation = db.rows("ai_evaluations")[0]
        assert evaluation["tool_version"] == 1
        assert "舊版構面" in json.dumps(evaluation["dimension_scores"], ensure_ascii=False)

    def test_locked_chat_bot_uses_old_instructions(self):
        from core.chat_room import load_bots

        db = make_db()
        db.tables["chat_room_bots"][0]["tool_version"] = 1
        db.tables["chat_room_bots"][0]["ai_tools"]["version"] = 3
        db.tables["ai_tool_versions"] = [{"id": "v1", "tool_id": BOT_A, "version": 1,
                                          "snapshot": {"system_prompt": "你是第一版的家長", "role_instruction": "舊角色"}}]
        bots = {b.tool_id: b for b in load_bots(db, STEP)}
        assert bots[BOT_A].system_prompt == "你是第一版的家長"
        assert bots[BOT_B].system_prompt == "你是IEP 小幫手"   # 沒停在舊版的照常
