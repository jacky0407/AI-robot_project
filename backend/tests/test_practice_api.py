"""
單元測試：練習 API (api/practice.py)

測試策略：
  - 用 FakeSupabase 取代真實資料庫，不連線、不寫入
  - Mock EvaluatorAgent 的 LLM，不消耗 Gemini quota
  - 重點涵蓋 docs/KNOWN_GAPS.md 第 2、4 項的回歸情境
"""

import json
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import main
from api import practice


# ──────────────────────────────────────────
# Fake Supabase
# ──────────────────────────────────────────

class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeTable:
    """模擬 supabase-py 的鏈式查詢介面，並記錄呼叫內容供斷言。"""

    def __init__(self, name: str, db: "FakeSupabase"):
        self.name = name
        self.db = db
        self.op = None
        self.payload = None
        self.count_mode = None
        self.selected = ""
        self.filters: list[tuple] = []

    def select(self, *args, **kwargs):
        self.op = "select"
        self.selected = args[0] if args else ""
        self.count_mode = kwargs.get("count")
        return self

    def insert(self, payload, **kwargs):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload, **kwargs):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def is_(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append((column, tuple(values)))
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        self.db.calls.append({
            "table": self.name,
            "op": self.op,
            "selected": self.selected,
            "filters": list(self.filters),
            "payload": self.payload,
        })
        return self.db.respond(self)


def _split_columns(selected: str) -> list[str]:
    """把 PostgREST 的 select 字串切成頂層欄位名（忽略巢狀關聯的內層欄位）。"""
    columns, depth, buf = [], 0, ""
    for ch in selected:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            columns.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        columns.append(buf.strip())
    return [c.split("(")[0].strip() for c in columns if c]


def _project(row: dict, selected: str) -> dict:
    """模擬 PostgREST：只回傳 select() 有指定的欄位。"""
    if not selected or selected.strip() == "*":
        return dict(row)
    columns = _split_columns(selected)
    if "*" in columns:
        return dict(row)
    return {k: v for k, v in row.items() if k in columns}


class FakeSupabase:
    def __init__(
        self,
        step_row: dict,
        prev_attempts: list,
        hints_count: int = 0,
        *,
        hint_logs: dict[str, int] | None = None,
        fail_tutor_action_update: bool = False,
    ):
        self.step_row = step_row
        self.prev_attempts = prev_attempts
        self.hints_count = hints_count
        # 給了 hint_logs（attempt_id → 提示數）時，prompt_logs 的 count
        # 會依查詢條件真的去加總，才能抓到「只算上一次作答」這類錯誤
        self.hint_logs = hint_logs
        self.fail_tutor_action_update = fail_tutor_action_update
        self.calls: list[dict] = []

    def table(self, name):
        return FakeTable(name, self)

    def respond(self, q: FakeTable) -> FakeResult:
        key = (q.name, q.op)
        if key == ("module_steps", "select"):
            return FakeResult(self.step_row)              # .single() → dict
        if key == ("step_attempts", "select"):
            # 依 select() 指定的欄位投影，未被選取的欄位不會回傳
            # —— 這樣才能真實重現「只選了 attempt_number 卻讀 id」的錯誤
            return FakeResult([_project(row, q.selected) for row in self.prev_attempts])
        if key == ("prompt_logs", "select"):
            if self.hint_logs is None:
                return FakeResult([], count=self.hints_count)
            ids: set[str] = set()
            for column, value in q.filters:
                if column == "attempt_id":
                    ids.update(value if isinstance(value, tuple) else (value,))
            return FakeResult([], count=sum(self.hint_logs.get(i, 0) for i in ids))
        if key == ("step_attempts", "insert"):
            return FakeResult([{"id": "attempt-new"}])
        if key == ("ai_evaluations", "insert"):
            return FakeResult([{"id": "eval-new"}])
        if key == ("prompt_logs", "insert"):
            return FakeResult([{"id": "log-new"}])
        if key == ("step_attempts", "update"):
            if self.fail_tutor_action_update and "tutor_action" in (q.payload or {}):
                raise RuntimeError('column "tutor_action" does not exist')
            return FakeResult([{"id": "attempt-new"}])
        return FakeResult([])

    def find(self, table: str, op: str) -> list[dict]:
        return [c for c in self.calls if c["table"] == table and c["op"] == op]


# ──────────────────────────────────────────
# 測試資料
# ──────────────────────────────────────────

# 三個構面，配分 40 / 30 / 30，沒有 levels —— 這正是資料庫的真實格式
STEP_ROW = {
    "id": "step-1",
    "step_order": 1,
    "step_title": "步驟1：案例資料整理",
    "pass_score": 70,                       # ← 門檻在 module_steps
    "ai_tools": {
        "id": "tool-1",
        "title": "案例資料整理教練",
        "system_prompt": "你是評分助理",
        # 注意：ai_tools 沒有 pass_score 欄位
        "rubric_criteria": [
            {"dimension": "客觀事實辨識", "max_score": 40, "description": "能擷取具體行為"},
            {"dimension": "推論與假設區分", "max_score": 30, "description": "能分開標示推論"},
            {"dimension": "缺漏資訊提問", "max_score": 30, "description": "能指出缺漏訊息"},
        ],
    },
}

CLEAN_ANSWER = "小明在積木角能專注建構約二十分鐘，展現良好的視覺空間能力。"

BODY = {
    "user_id": "11111111-1111-1111-1111-111111111111",
    "module_id": "22222222-2222-2222-2222-222222222222",
    "step_id": "step-1",
    "content": CLEAN_ANSWER,
}


def dim_response(name: str, score: int) -> str:
    return json.dumps({
        "dimension": name, "score": score, "max_score": 4,
        "reason": "測試理由", "evidence": "積木角",
    })


SYNTH_RESPONSE = json.dumps({"overall_feedback": "整體不錯", "confidence": 0.9})


def mock_llm(responses: list[str]):
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=[AIMessage(content=r) for r in responses])
    return llm


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 原文切成 [(event, data), ...]"""
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event is not None:
            events.append((event, data))
    return events


def status_updates(fake_db: FakeSupabase) -> list[str]:
    """step_attempts 的 status 更新（另有一筆 tutor_action 更新，這裡排除）"""
    return [
        c["payload"]["status"]
        for c in fake_db.find("step_attempts", "update")
        if "status" in c["payload"]
    ]


def submit(fake_db: FakeSupabase, llm_responses: list[str], body: dict = None):
    """跑一次 submit，回傳 (response, events)"""
    client = TestClient(main.app)
    with patch.object(practice, "get_supabase", return_value=fake_db):
        practice.tutor_agent._evaluator._llm = mock_llm(llm_responses)
        res = client.post(
            "/api/practice/sessions/test-session/submit",
            json=body or BODY,
        )
    events = parse_sse(res.text) if res.status_code == 200 else []
    return res, events


# ──────────────────────────────────────────
# 個資偵測
# ──────────────────────────────────────────

class TestPIIGate:

    def test_pii_blocks_submission(self):
        """作答含手機號碼時應直接 400，不得進入評分流程"""
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        body = {**BODY, "content": "請聯絡家長 0912-345-678 討論"}
        res, _ = submit(fake_db, [], body)

        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "PII_DETECTED"

    def test_pii_does_not_write_to_database(self):
        """被個資攔下時不應留下任何 step_attempts 記錄"""
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        body = {**BODY, "content": "身分證 A123456789"}
        submit(fake_db, [], body)

        assert fake_db.find("step_attempts", "insert") == []


# ──────────────────────────────────────────
# 第一次提交
# ──────────────────────────────────────────

class TestFirstAttempt:

    def test_first_attempt_streams_evaluation(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        responses = [dim_response("客觀事實辨識", 3),
                     dim_response("推論與假設區分", 3),
                     dim_response("缺漏資訊提問", 3),
                     SYNTH_RESPONSE]
        res, events = submit(fake_db, responses)

        assert res.status_code == 200
        names = [e for e, _ in events]
        assert names[0] == "score_start"
        assert "tutor_decision" in names
        assert names[-1] == "score_complete"

    def test_first_attempt_does_not_query_prompt_logs(self):
        """
        回歸測試（KNOWN_GAPS #2）：
        沒有前次嘗試時不可以去查 prompt_logs，
        舊版會拿字串 "none" 去比對 UUID 欄位而讓查詢報錯。
        """
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        responses = [dim_response("A", 3), dim_response("B", 3),
                     dim_response("C", 3), SYNTH_RESPONSE]
        submit(fake_db, responses)

        assert fake_db.find("prompt_logs", "select") == []

    def test_attempt_number_starts_at_one(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        responses = [dim_response("A", 3), dim_response("B", 3),
                     dim_response("C", 3), SYNTH_RESPONSE]
        submit(fake_db, responses)

        inserted = fake_db.find("step_attempts", "insert")[0]["payload"]
        assert inserted["attempt_number"] == 1
        assert inserted["status"] == "submitted"


# ──────────────────────────────────────────
# 第二次提交（原本會 KeyError）
# ──────────────────────────────────────────

class TestSecondAttempt:

    PREV = [{
        "id": "attempt-prev",
        "attempt_number": 1,
        "ai_evaluations": [{"total_score": 55, "dimension_scores": []}],
    }]

    def test_second_attempt_does_not_raise(self):
        """
        回歸測試（KNOWN_GAPS #2）：
        舊版只 select("attempt_number") 卻讀 ["id"]，第二次提交必定 KeyError。
        """
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=self.PREV, hints_count=1)
        responses = [dim_response("A", 3), dim_response("B", 3),
                     dim_response("C", 3), SYNTH_RESPONSE]
        res, _ = submit(fake_db, responses)

        assert res.status_code == 200

    def test_prompt_logs_filtered_by_previous_attempt_id(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=self.PREV, hints_count=2)
        responses = [dim_response("A", 3), dim_response("B", 3),
                     dim_response("C", 3), SYNTH_RESPONSE]
        submit(fake_db, responses)

        call = fake_db.find("prompt_logs", "select")[0]
        assert ("attempt_id", ("attempt-prev",)) in call["filters"]

    def test_attempt_number_increments(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=self.PREV, hints_count=1)
        responses = [dim_response("A", 3), dim_response("B", 3),
                     dim_response("C", 3), SYNTH_RESPONSE]
        submit(fake_db, responses)

        inserted = fake_db.find("step_attempts", "insert")[0]["payload"]
        assert inserted["attempt_number"] == 2

    def test_previous_score_is_passed_to_tutor(self):
        """上次 55 分 → TutorAgent 應決定先給提示而非直接評分"""
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=self.PREV, hints_count=0)
        _, events = submit(fake_db, [])

        decision = dict(events)["tutor_decision"]
        assert decision["last_score_percent"] == 55.0
        assert decision["action"] == "give_hint"


# ──────────────────────────────────────────
# 通過門檻來源（KNOWN_GAPS #4）與加權計分（#5）
# ──────────────────────────────────────────

class TestPassThresholdAndWeighting:

    # 配分 40 / 30 / 30，得分 2/4、4/4、3/4
    #   → (0.5*40 + 1.0*30 + 0.75*30) / 100 * 100 = 72.5
    # 門檻 70（module_steps.pass_score）→ 通過
    # 若誤用舊版預設 75（ai_tools.pass_score）→ 不通過
    RESPONSES = [
        dim_response("客觀事實辨識", 2),
        dim_response("推論與假設區分", 4),
        dim_response("缺漏資訊提問", 3),
        SYNTH_RESPONSE,
    ]

    def test_weighted_percentage_is_correct(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        _, events = submit(fake_db, self.RESPONSES)

        score = dict(events)["score_complete"]
        assert score["percentage"] == pytest.approx(72.5)
        assert score["max_total_score"] == 100.0

    def test_pass_threshold_comes_from_module_steps(self):
        """
        回歸測試（KNOWN_GAPS #4）：
        門檻要讀 module_steps.pass_score（70），
        舊版讀 ai_tools.pass_score（不存在）而恆為預設 75，
        72.5 分會被誤判為不通過。
        """
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        _, events = submit(fake_db, self.RESPONSES)

        assert dict(events)["score_complete"]["passed"] is True

    def test_rubric_without_levels_uses_four_point_scale(self):
        """
        回歸測試（KNOWN_GAPS #5）：
        max_score=40 是配分，不可展開成 40 個等級。
        各構面回報的 max_score 應為 4。
        """
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        _, events = submit(fake_db, self.RESPONSES)

        dimensions = [d for e, d in events if e == "dimension_score"]
        assert len(dimensions) == 3
        assert all(d["max_score"] == 4 for d in dimensions)
        assert [d["weight"] for d in dimensions] == [40, 30, 30]


# ──────────────────────────────────────────
# 評分結果寫入
# ──────────────────────────────────────────

class TestPersistence:

    RESPONSES = [
        dim_response("客觀事實辨識", 4),
        dim_response("推論與假設區分", 4),
        dim_response("缺漏資訊提問", 4),
        SYNTH_RESPONSE,
    ]

    def test_ai_evaluation_is_written(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit(fake_db, self.RESPONSES)

        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert payload["attempt_id"] == "attempt-new"
        assert payload["total_score"] == 100
        assert len(payload["dimension_scores"]) == 3

    def test_total_score_is_integer_for_db_column(self):
        """ai_evaluations.total_score 是 INT，寫入前必須轉整數"""
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit(fake_db, [dim_response("A", 2), dim_response("B", 4),
                         dim_response("C", 3), SYNTH_RESPONSE])

        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert isinstance(payload["total_score"], int)
        assert payload["total_score"] == 72        # 72.5 四捨五入

    def test_status_updated_to_passed(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit(fake_db, self.RESPONSES)

        assert status_updates(fake_db) == ["passed"]

    def test_status_updated_to_revision_required(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit(fake_db, [dim_response("A", 1), dim_response("B", 1),
                         dim_response("C", 1), SYNTH_RESPONSE])

        assert status_updates(fake_db) == ["revision_required"]


# ==============================================================================
# LLM 邏輯系統：confidence 落庫、真實佐證、錯誤分類、分層提示
# ==============================================================================

from unittest.mock import AsyncMock as _AsyncMock

from core.ai.error_detector import ErrorDetector
from core.ai.hint_engine import HintEngine

TAXONOMY = [
    {"code": "diagnosis_only", "label": "僅列診斷名稱",
     "description": "只寫障礙類別", "severity": "high"},
]

TEACHING_STRATEGY = {
    "hints": [
        {"level": 1, "trigger": "score_below_threshold", "content": "你觀察到哪個行為？"},
        {"level": 2, "trigger": "student_request", "content": "包含情境、行為、頻率"},
    ],
    "personalize": False,
}


def step_row_with(**tool_overrides) -> dict:
    """複製 STEP_ROW 並覆寫 ai_tools 欄位"""
    row = json.loads(json.dumps(STEP_ROW))
    row["ai_tools"].update(tool_overrides)
    return row


def _fake_message(content):
    class _M:
        def __init__(self, c):
            self.content = c
    return _M(content)


def submit_with_agents(
    fake_db,
    llm_responses,
    *,
    body=None,
    detector_llm=None,
    hint_llm=None,
):
    """跑一次 submit，並替換錯誤偵測器與提示引擎的 LLM"""
    client = TestClient(main.app)
    with patch.object(practice, "get_supabase", return_value=fake_db):
        practice.tutor_agent._evaluator._llm = mock_llm(llm_responses)
        practice.error_detector = ErrorDetector(llm=detector_llm or _AsyncMock())
        practice.tutor_agent._hints = HintEngine(llm=hint_llm or _AsyncMock())
        res = client.post(
            "/api/practice/sessions/test-session/submit",
            json=body or BODY,
        )
    events = parse_sse(res.text) if res.status_code == 200 else []
    return res, events


FULL_RESPONSES = [
    dim_response("客觀事實辨識", 3),
    dim_response("推論與假設區分", 3),
    dim_response("缺漏資訊提問", 3),
    json.dumps({"overall_feedback": "整體不錯", "confidence": 0.42}),
]


class TestConfidencePersistence:

    def test_confidence_is_written(self):
        """
        api.md 的規格有 confidence，舊 schema 沒有欄位，Agent 算完就丟掉。
        現在必須落庫，教師端才看得到「AI 對這次評分沒把握」。
        """
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit_with_agents(fake_db, FULL_RESPONSES)

        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert payload["confidence"] == pytest.approx(0.42)

    def test_low_confidence_flags_teacher_review(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit_with_agents(fake_db, FULL_RESPONSES)

        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert payload["needs_teacher_review"] is True

    def test_high_confidence_does_not_flag(self):
        responses = FULL_RESPONSES[:-1] + [
            json.dumps({"overall_feedback": "很清楚", "confidence": 0.95})
        ]
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit_with_agents(fake_db, responses)

        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert payload["needs_teacher_review"] is False


class TestEvidenceText:

    def test_stores_ai_evidence_not_raw_answer(self):
        """
        回歸測試：evidence_text 舊版存的是學生作答前 200 字，那不是證據。
        應該存 AI 各構面實際引用的原文。
        """
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit_with_agents(fake_db, FULL_RESPONSES)

        evidence = fake_db.find("ai_evaluations", "insert")[0]["payload"]["evidence_text"]
        assert evidence != CLEAN_ANSWER[:200]
        assert "積木角" in evidence          # dim_response 裡的 evidence
        assert "客觀事實辨識" in evidence     # 標注是哪個構面


class TestErrorDetection:

    def test_no_taxonomy_skips_llm(self):
        """教授沒定義 error_taxonomy 時不該多燒一次 API 額度"""
        detector_llm = _AsyncMock()
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit_with_agents(fake_db, FULL_RESPONSES, detector_llm=detector_llm)

        assert detector_llm.ainvoke.await_count == 0
        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert payload["detected_errors"] == []

    def test_detected_errors_written_and_streamed(self):
        detector_llm = _AsyncMock()
        detector_llm.ainvoke = _AsyncMock(return_value=_fake_message(json.dumps({
            "detected": [{"code": "diagnosis_only", "evidence": "「診斷為自閉症」",
                          "explanation": "只寫診斷"}]
        })))
        fake_db = FakeSupabase(step_row_with(error_taxonomy=TAXONOMY), prev_attempts=[])
        _, events = submit_with_agents(fake_db, FULL_RESPONSES, detector_llm=detector_llm)

        payload = fake_db.find("ai_evaluations", "insert")[0]["payload"]
        assert [e["code"] for e in payload["detected_errors"]] == ["diagnosis_only"]
        assert payload["detected_errors"][0]["severity"] == "high"

        # 前端要能即時顯示
        assert "errors_detected" in dict(events)

    def test_detection_failure_does_not_break_scoring(self):
        """錯誤分類掛掉時，評分結果仍要寫入"""
        detector_llm = _AsyncMock()
        detector_llm.ainvoke = _AsyncMock(side_effect=RuntimeError("掛了"))
        fake_db = FakeSupabase(step_row_with(error_taxonomy=TAXONOMY), prev_attempts=[])
        res, _ = submit_with_agents(fake_db, FULL_RESPONSES, detector_llm=detector_llm)

        assert res.status_code == 200
        assert fake_db.find("ai_evaluations", "insert")


class TestHintPersistence:

    PREV = [{
        "id": "attempt-prev",
        "attempt_number": 1,
        "user_input_content": "小明有自閉症。",
        "ai_evaluations": [{
            "total_score": 45,
            "dimension_scores": [
                {"dimension": "功能性描述", "score": 1, "max_score": 4, "reason": "只列診斷"},
            ],
        }],
    }]

    def test_teacher_hint_is_used_and_logged_with_trigger(self):
        """上次 45 分 → GIVE_HINT；應該用教授寫的提示並記下 trigger / source"""
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=TEACHING_STRATEGY),
            prev_attempts=self.PREV,
            hints_count=0,
        )
        _, events = submit_with_agents(fake_db, [])

        hint = dict(events)["hint"]
        assert hint["content"] == "你觀察到哪個行為？"
        assert hint["source"] == "teacher"

        logged = fake_db.find("prompt_logs", "insert")[0]["payload"]
        assert logged["hint_trigger"] == "score_below_threshold"
        assert logged["hint_source"] == "teacher"

    def test_second_level_needs_student_request(self):
        """第 2 層是 student_request，學生沒要就不給 → 改為直接評分"""
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=TEACHING_STRATEGY),
            prev_attempts=self.PREV,
            hints_count=1,
        )
        _, events = submit_with_agents(fake_db, FULL_RESPONSES)

        names = [e for e, _ in events]
        assert "hint" not in names
        assert "score_complete" in names

    def test_student_requested_hint_unlocks_next_level(self):
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=TEACHING_STRATEGY),
            prev_attempts=self.PREV,
            hints_count=1,
        )
        _, events = submit_with_agents(
            fake_db, [], body={**BODY, "request_hint": True}
        )

        hint = dict(events)["hint"]
        assert hint["level"] == 2
        assert hint["trigger"] == "student_request"


# ──────────────────────────────────────────
# 分層提示要能逐層往下（整個步驟累計）
# ──────────────────────────────────────────

AUTO_THREE_LEVELS = {
    "hints": [
        {"level": 1, "trigger": "score_below_threshold", "content": "第一層：你觀察到哪個行為？"},
        {"level": 2, "trigger": "score_below_threshold", "content": "第二層：補上情境與頻率"},
        {"level": 3, "trigger": "score_below_threshold", "content": "第三層：參考句型"},
    ],
    "max_auto_hints": 3,
    "personalize": False,
}

LOW_DIMS = [{"dimension": "客觀事實辨識", "score": 1, "max_score": 4, "reason": "只列診斷"}]

# 新到舊：第 3 次評分 45 分；第 2 次只拿到提示（沒評分）；第 1 次評分 40 分
HISTORY_AFTER_ONE_HINT = [
    {"id": "attempt-3", "attempt_number": 3, "user_input_content": "改過的作答",
     "ai_evaluations": [{"total_score": 45, "dimension_scores": LOW_DIMS}]},
    {"id": "attempt-2", "attempt_number": 2, "user_input_content": "第二版",
     "ai_evaluations": []},
    {"id": "attempt-1", "attempt_number": 1, "user_input_content": "小明有自閉症。",
     "ai_evaluations": [{"total_score": 40, "dimension_scores": LOW_DIMS}]},
]


class TestHintProgression:

    def test_hints_used_counts_whole_step_not_only_last_attempt(self):
        """
        回歸：舊版只算「上一次作答」的 prompt_logs。給提示那次不評分，
        下一輪上一次作答換成評分那次 → hints_used 又變 0 → 永遠只拿到第 1 層。
        """
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=AUTO_THREE_LEVELS),
            prev_attempts=HISTORY_AFTER_ONE_HINT,
            hint_logs={"attempt-2": 1},      # 第 1 層提示記在第 2 次作答底下
        )
        _, events = submit_with_agents(fake_db, [])

        hint = dict(events)["hint"]
        assert hint["level"] == 2
        assert hint["content"].startswith("第二層")

    def test_prompt_logs_queried_for_all_attempts_of_step(self):
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=AUTO_THREE_LEVELS),
            prev_attempts=HISTORY_AFTER_ONE_HINT,
            hint_logs={"attempt-2": 1},
        )
        submit_with_agents(fake_db, [])

        call = fake_db.find("prompt_logs", "select")[0]
        assert ("attempt_id", ("attempt-3", "attempt-2", "attempt-1")) in call["filters"]

    def test_all_levels_used_falls_back_to_evaluation(self):
        """三層都給過了 → 不再硬擠提示，改為直接評分"""
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=AUTO_THREE_LEVELS),
            prev_attempts=HISTORY_AFTER_ONE_HINT,
            hint_logs={"attempt-1": 1, "attempt-2": 2},
        )
        _, events = submit_with_agents(fake_db, FULL_RESPONSES)

        names = [e for e, _ in events]
        assert "hint" not in names
        assert "score_complete" in names


# ──────────────────────────────────────────
# TutorAgent 決策落庫（給教師複核佇列用）
# ──────────────────────────────────────────

class TestTutorActionPersistence:

    LOW_PREV = [{"id": "attempt-prev", "attempt_number": 1,
                 "ai_evaluations": [{"total_score": 45, "dimension_scores": LOW_DIMS}]}]

    def _updates(self, fake_db) -> list[dict]:
        return [c["payload"] for c in fake_db.find("step_attempts", "update")]

    def test_evaluate_decision_is_recorded(self):
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[])
        submit_with_agents(fake_db, FULL_RESPONSES)

        assert {"tutor_action": "evaluate"} in self._updates(fake_db)

    def test_hint_only_attempt_becomes_revision_required(self):
        """只給提示沒評分的作答，狀態不能永遠停在 submitted"""
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=TEACHING_STRATEGY),
            prev_attempts=self.LOW_PREV,
        )
        _, events = submit_with_agents(fake_db, [])

        assert "hint" in dict(events)
        updates = self._updates(fake_db)
        assert {"tutor_action": "give_hint"} in updates
        assert {"status": "revision_required"} in updates
        assert fake_db.find("ai_evaluations", "insert") == []

    def test_escalation_is_recorded_for_teacher_queue(self):
        """已被評分 3 次且仍低於 60 分 → ESCALATE，要記下來讓老師看得到"""
        prev = [
            {"id": f"attempt-{n}", "attempt_number": n,
             "ai_evaluations": [{"total_score": 30, "dimension_scores": LOW_DIMS}]}
            for n in (3, 2, 1)
        ]
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=prev, hints_count=1)
        _, events = submit_with_agents(fake_db, [])

        assert "escalation" in dict(events)
        updates = self._updates(fake_db)
        assert {"tutor_action": "escalate"} in updates
        assert {"status": "revision_required"} in updates

    def test_missing_column_does_not_break_scoring(self):
        """還沒跑 migration 003 時，寫 tutor_action 失敗不能讓評分中斷"""
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=[], fail_tutor_action_update=True)
        _, events = submit_with_agents(fake_db, FULL_RESPONSES)

        names = [e for e, _ in events]
        assert "error" not in names
        assert "score_complete" in names
        assert fake_db.find("ai_evaluations", "insert")


# ──────────────────────────────────────────
# GET /hints
# ──────────────────────────────────────────

def get_hints(fake_db, *, reveal: bool, hint_llm=None):
    client = TestClient(main.app)
    with patch.object(practice, "get_supabase", return_value=fake_db):
        practice.hint_engine = HintEngine(llm=hint_llm or _AsyncMock())
        return client.get(
            "/api/practice/sessions/test-session/hints",
            params={"user_id": BODY["user_id"], "step_id": "step-1", "reveal": reveal},
        )


class TestHintsEndpoint:

    def test_peek_does_not_log_or_consume(self):
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=AUTO_THREE_LEVELS),
            prev_attempts=HISTORY_AFTER_ONE_HINT,
            hint_logs={"attempt-2": 1},
        )
        res = get_hints(fake_db, reveal=False)

        data = res.json()["data"]
        assert data["hints_used"] == 1          # 整個步驟累計
        assert data["next_level_available"] is True
        assert fake_db.find("prompt_logs", "insert") == []

    def test_reveal_gives_next_level_and_logs_on_latest_attempt(self):
        fake_db = FakeSupabase(
            step_row_with(teaching_strategy=AUTO_THREE_LEVELS),
            prev_attempts=HISTORY_AFTER_ONE_HINT,
            hint_logs={"attempt-2": 1},
        )
        res = get_hints(fake_db, reveal=True)

        assert res.json()["data"]["hint"]["level"] == 2
        logged = fake_db.find("prompt_logs", "insert")[0]["payload"]
        assert logged["attempt_id"] == "attempt-3"
        assert logged["hint_trigger"] == "student_request"

    def test_generated_hint_uses_latest_evaluated_attempt(self):
        """
        教授沒設定提示時要依最弱構面生成。上一次若只拿到提示（沒評分），
        要往前找有評分的作答，而不是回「沒有提示可用」。
        """
        history = [
            {"id": "attempt-2", "attempt_number": 2, "user_input_content": "第二版",
             "ai_evaluations": []},
            {"id": "attempt-1", "attempt_number": 1, "user_input_content": "小明有自閉症。",
             "ai_evaluations": [{"total_score": 40, "dimension_scores": LOW_DIMS}]},
        ]
        fake_db = FakeSupabase(STEP_ROW, prev_attempts=history, hint_logs={})
        res = get_hints(fake_db, reveal=False)

        assert res.json()["data"]["next_level_available"] is True
