"""
單元測試：機器人記憶 (core/ai/memory.py)

重點：
  - 記憶從既有資料表組出：本步驟歷程 + 前面步驟成果
  - 只收已發布的教師複核，未發布的不能讓 AI 先說出來
  - 前面步驟取「通過優先、否則最新」的版本，且不讀到本步驟或後面步驟
  - 教授可以逐支機器人關掉記憶或調整筆數
  - 查詢失敗回空記憶，不擋評分
"""

from core.ai.memory import (
    ATTEMPT_SELECT,
    PRIOR_STEP_SELECT,
    BotMemory,
    MemoryConfig,
    load_bot_memory,
    parse_memory_config,
)

USER_ID = "11111111-1111-1111-1111-111111111111"


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.selected = ""
        self.filters = []
        self.limit_n = None

    def select(self, selected, **kwargs):
        self.selected = selected
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def execute(self):
        self.db.calls.append(self)
        if self.db.fail:
            raise RuntimeError("connection reset")
        if self.selected == ATTEMPT_SELECT:
            rows = self.db.step_rows
            return FakeResult(rows[: self.limit_n] if self.limit_n else rows)
        if self.selected == PRIOR_STEP_SELECT:
            return FakeResult(self.db.module_rows)
        return FakeResult([])


class FakeDB:
    def __init__(self, step_rows=None, module_rows=None, fail=False):
        self.step_rows = step_rows or []        # 新到舊，與真實查詢的排序一致
        self.module_rows = module_rows or []
        self.fail = fail
        self.calls: list[FakeQuery] = []

    def table(self, name):
        return FakeQuery(self, name)


def step_row(step_order=3, teaching_strategy=None):
    return {
        "id": "step-3",
        "module_id": "module-1",
        "step_order": step_order,
        "ai_tools": {"teaching_strategy": teaching_strategy or {}},
    }


def attempt(number, score=None, dims=None, errors=None, feedback="", hints=None, reviews=None):
    evaluation = []
    if score is not None:
        evaluation = [{
            "total_score": score,
            "dimension_scores": dims or [],
            "detected_errors": errors or [],
            "feedback_text": feedback,
        }]
    return {
        "id": f"a{number}",
        "attempt_number": number,
        "status": "revision_required",
        "ai_evaluations": evaluation,
        "teacher_reviews": reviews or [],
        "prompt_logs": hints or [],
    }


def prior(step_order, number, status, content, title=None):
    return {
        "step_id": f"step-{step_order}",
        "attempt_number": number,
        "status": status,
        "user_input_content": content,
        "module_steps": {"step_order": step_order, "step_title": title or f"步驟{step_order}"},
    }


DIMS = [
    {"dimension": "客觀事實辨識", "score": 3, "max_score": 4},
    {"dimension": "功能性描述", "score": 1, "max_score": 4},
]
LABEL_ERROR = {"code": "diagnosis_only", "label": "僅列診斷名稱"}


# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────

class TestParseMemoryConfig:

    def test_missing_config_defaults_to_enabled(self):
        assert parse_memory_config(None) == MemoryConfig()
        assert parse_memory_config({"hints": []}) == MemoryConfig()

    def test_reads_professor_settings(self):
        config = parse_memory_config(
            {"memory": {"enabled": False, "max_attempts": 5, "include_prior_steps": False}}
        )
        assert config == MemoryConfig(enabled=False, max_attempts=5, include_prior_steps=False)

    def test_invalid_values_fall_back_to_defaults(self):
        config = parse_memory_config(
            {"memory": {"enabled": "no", "max_attempts": "3", "include_prior_steps": 1}}
        )
        assert config == MemoryConfig()

    def test_max_attempts_is_clamped(self):
        assert parse_memory_config({"memory": {"max_attempts": 999}}).max_attempts == 10
        assert parse_memory_config({"memory": {"max_attempts": -2}}).max_attempts == 0

    def test_builder_format_with_hints_is_read_by_both_parsers(self):
        """建立機器人頁面（admin/tools/new）把提示與記憶寫在同一個 teaching_strategy"""
        from core.ai.hint_engine import parse_teaching_strategy

        strategy = {
            "hints": [{"level": 1, "trigger": "score_below_threshold", "content": "先找出具體行為"}],
            "max_auto_hints": 2,
            "personalize": True,
            "memory": {"enabled": True, "max_attempts": 5, "include_prior_steps": False},
        }
        assert parse_memory_config(strategy) == MemoryConfig(enabled=True, max_attempts=5, include_prior_steps=False)
        plan = parse_teaching_strategy(strategy)
        assert plan.total_levels == 1 and plan.max_auto_hints == 2

    def test_builder_format_without_hints_keeps_default_hint_plan(self):
        """沒填提示時頁面只寫 memory，提示仍走「AI 依最弱構面生成」的預設"""
        from core.ai.hint_engine import parse_teaching_strategy

        strategy = {"memory": {"enabled": False, "max_attempts": 3, "include_prior_steps": True}}
        assert parse_memory_config(strategy).enabled is False
        assert parse_teaching_strategy(strategy).total_levels == parse_teaching_strategy({}).total_levels


# ──────────────────────────────────────────
# 本步驟歷程
# ──────────────────────────────────────────

class TestStepAttempts:

    def test_builds_attempts_oldest_first_with_scores_and_errors(self):
        db = FakeDB(step_rows=[
            attempt(2, score=55, dims=DIMS, errors=[LABEL_ERROR], feedback="還缺頻率"),
            attempt(1, score=40, dims=DIMS, errors=[LABEL_ERROR]),
        ])
        memory = load_bot_memory(db, USER_ID, step_row(step_order=1))

        assert [a.attempt_number for a in memory.attempts] == [1, 2]
        assert memory.attempts[1].score == 55
        assert memory.attempts[1].weakest_dimension == "功能性描述"
        assert memory.attempts[1].feedback == "還缺頻率"
        assert memory.recurring_errors() == ["僅列診斷名稱"]

    def test_hint_only_attempt_has_no_score(self):
        db = FakeDB(step_rows=[
            attempt(2, hints=[{"hint_level": 1, "hint_content": "你觀察到哪個行為？"}]),
        ])
        memory = load_bot_memory(db, USER_ID, step_row(step_order=1))

        assert memory.attempts[0].score is None
        assert memory.attempts[0].hints == ["你觀察到哪個行為？"]
        assert "未評分" in memory.to_prompt_text()
        assert "已給過的提示：你觀察到哪個行為？" in memory.to_prompt_text()

    def test_only_published_teacher_review_is_remembered(self):
        db = FakeDB(step_rows=[
            attempt(2, score=60, reviews=[
                {"final_score": 80, "final_feedback": "其實寫得不錯", "is_published": False},
            ]),
            attempt(1, score=50, reviews=[
                {"final_score": 65, "final_feedback": "請補上情境", "is_published": True},
            ]),
        ])
        memory = load_bot_memory(db, USER_ID, step_row(step_order=1))
        text = memory.to_prompt_text()

        assert memory.attempts[0].teacher_score == 65
        assert memory.attempts[1].teacher_score is None
        assert "請補上情境" in text
        assert "其實寫得不錯" not in text

    def test_respects_max_attempts(self):
        db = FakeDB(step_rows=[attempt(n, score=50) for n in (5, 4, 3, 2, 1)])
        memory = load_bot_memory(db, USER_ID, step_row(
            step_order=1, teaching_strategy={"memory": {"max_attempts": 2}},
        ))

        assert [a.attempt_number for a in memory.attempts] == [4, 5]

    def test_queries_only_the_given_user(self):
        db = FakeDB(step_rows=[attempt(1, score=50)])
        load_bot_memory(db, USER_ID, step_row())

        user_filters = {v for q in db.calls for c, v in q.filters if c == "user_id"}
        assert user_filters == {USER_ID}


# ──────────────────────────────────────────
# 前面步驟成果
# ──────────────────────────────────────────

class TestPriorSteps:

    def test_prefers_passed_version_and_skips_current_and_later_steps(self):
        db = FakeDB(module_rows=[
            prior(1, 3, "revision_required", "第一步最新但沒過"),
            prior(1, 2, "passed", "第一步通過版"),
            prior(2, 1, "revision_required", "第二步唯一一次"),
            prior(3, 1, "passed", "本步驟自己"),
            prior(4, 1, "passed", "後面步驟"),
        ])
        memory = load_bot_memory(db, USER_ID, step_row(step_order=3))

        assert [(p.step_order, p.content, p.passed) for p in memory.prior_steps] == [
            (1, "第一步通過版", True),
            (2, "第二步唯一一次", False),
        ]

    def test_first_step_does_not_query_prior_steps(self):
        db = FakeDB()
        load_bot_memory(db, USER_ID, step_row(step_order=1))

        assert all(q.selected != PRIOR_STEP_SELECT for q in db.calls)

    def test_long_content_is_truncated(self):
        db = FakeDB(module_rows=[prior(1, 1, "passed", "字" * 1000)])
        memory = load_bot_memory(db, USER_ID, step_row(step_order=2))

        assert len(memory.prior_steps[0].content) == 401
        assert memory.prior_steps[0].content.endswith("…")

    def test_professor_can_turn_off_prior_steps(self):
        db = FakeDB(module_rows=[prior(1, 1, "passed", "第一步")])
        memory = load_bot_memory(db, USER_ID, step_row(
            step_order=2, teaching_strategy={"memory": {"include_prior_steps": False}},
        ))

        assert memory.prior_steps == []


# ──────────────────────────────────────────
# 關閉與降級
# ──────────────────────────────────────────

class TestDisabledAndFailures:

    def test_disabled_memory_never_queries_db(self):
        db = FakeDB(step_rows=[attempt(1, score=50)])
        memory = load_bot_memory(db, USER_ID, step_row(
            teaching_strategy={"memory": {"enabled": False}},
        ))

        assert memory.is_empty
        assert db.calls == []

    def test_db_failure_returns_empty_memory(self):
        memory = load_bot_memory(FakeDB(fail=True), USER_ID, step_row())

        assert memory.is_empty
        assert memory.to_prompt_text() == ""

    def test_malformed_rows_are_ignored(self):
        db = FakeDB(
            step_rows=["not a dict", attempt(1, score=50, dims=[{"dimension": "x", "score": "?"}])],
            module_rows=[{"step_id": "s1", "module_steps": None}],
        )
        memory = load_bot_memory(db, USER_ID, step_row(step_order=2))

        assert [a.attempt_number for a in memory.attempts] == [1]
        assert memory.prior_steps == []


# ──────────────────────────────────────────
# 輸出格式
# ──────────────────────────────────────────

class TestOutput:

    def test_empty_memory_produces_no_text(self):
        assert BotMemory().to_prompt_text() == ""

    def test_prompt_text_contains_both_sections(self):
        db = FakeDB(
            step_rows=[attempt(1, score=50, dims=DIMS)],
            module_rows=[prior(1, 1, "passed", "小明在積木角專注20分鐘")],
        )
        text = load_bot_memory(db, USER_ID, step_row(step_order=2)).to_prompt_text()

        assert "【這位學生在本步驟的先前作答】" in text
        assert "第 1 次：50 分" in text
        assert "【這位學生在前面步驟的成果】" in text
        assert "步驟 1「步驟1」（已通過）：小明在積木角專注20分鐘" in text

    def test_event_data_omits_prior_step_content(self):
        db = FakeDB(
            step_rows=[attempt(1, score=50, hints=[{"hint_level": 1, "hint_content": "h"}])],
            module_rows=[prior(1, 1, "passed", "前一步全文")],
        )
        data = load_bot_memory(db, USER_ID, step_row(step_order=2)).to_event_data()

        assert data["attempts"][0]["hints_given"] == 1
        assert data["prior_steps"] == [{"step_order": 1, "step_title": "步驟1", "passed": True}]
        assert "前一步全文" not in str(data)


# ──────────────────────────────────────────
# 測試報告 2026-11-02 #4：沒評分的作答不能把重複錯誤擠出視窗
# ──────────────────────────────────────────

class TestRecurringErrorsWindow:

    def test_errors_outside_prompt_window_still_count_as_recurring(self):
        """
        實測資料：第 2、4 次都「過度依賴標籤」，第 3 次只給提示、第 5 次轉介老師（都沒評分）。
        記憶只放最近 3 次（3、4、5），但重複錯誤要看得到第 2 次。
        """
        db = FakeDB(step_rows=[
            attempt(5),
            attempt(4, score=50, errors=[LABEL_ERROR]),
            attempt(3, hints=[{"hint_level": 2, "hint_content": "h"}]),
            attempt(2, score=25, errors=[LABEL_ERROR]),
            attempt(1, score=100),
        ])
        memory = load_bot_memory(db, USER_ID, step_row(step_order=1))

        assert [a.attempt_number for a in memory.attempts] == [3, 4, 5]
        assert memory.recurring_errors() == ["僅列診斷名稱"]
        assert "重複出現的錯誤：僅列診斷名稱" in memory.to_prompt_text()
        assert memory.to_event_data()["recurring_errors"] == ["僅列診斷名稱"]

    def test_recurring_window_is_bounded(self):
        db = FakeDB(step_rows=[attempt(n, score=40) for n in range(15, 0, -1)])
        load_bot_memory(db, USER_ID, step_row(step_order=1))

        assert db.calls[0].limit_n == 10

    def test_single_occurrence_is_not_recurring(self):
        db = FakeDB(step_rows=[
            attempt(3, score=50, errors=[LABEL_ERROR]),
            attempt(2),
            attempt(1, score=40),
        ])
        memory = load_bot_memory(db, USER_ID, step_row(step_order=1))

        assert memory.recurring_errors() == []
