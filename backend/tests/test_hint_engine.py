"""
單元測試：分層提示引擎 (core/ai/hint_engine.py)

重點：
  - select_hint() 是純邏輯，完整測試解鎖規則
  - 教授寫的提示不可以被 AI 竄改意圖，個人化失敗要退回原模板
  - 範例型提示不做個人化（避免把「別照抄」的示範改成可照抄的答案）
  - 教授沒設定時要標記 source=generated，不可假裝是教學設計
"""

import pytest
from unittest.mock import AsyncMock

from core.ai.hint_engine import (
    HintEngine,
    HintPlan,
    parse_teaching_strategy,
    select_hint,
    weakest_dimension,
    TRIGGER_AUTO,
    TRIGGER_REQUEST,
    SOURCE_TEACHER,
    SOURCE_GENERATED,
    COPY_WARNING,
)


class FakeMessage:
    def __init__(self, content):
        self.content = content


TEACHER_STRATEGY = {
    "hints": [
        {"level": 1, "trigger": "score_below_threshold", "content": "你觀察到哪個具體行為？"},
        {"level": 2, "trigger": "score_below_threshold", "content": "功能性描述包含情境、行為、頻率"},
        {"level": 3, "trigger": "student_request", "content": "例如：在自由遊戲…",
         "is_example": True, "warn_copy": True},
    ],
    "max_auto_hints": 2,
}

DIMENSION_SCORES = [
    {"dimension": "客觀事實辨識", "score": 4, "max_score": 4, "reason": "很好"},
    {"dimension": "功能性描述", "score": 1, "max_score": 4, "reason": "只列了診斷"},
    {"dimension": "去標籤化用語", "score": 3, "max_score": 4, "reason": "尚可"},
]


# ──────────────────────────────────────────
# 解析 teaching_strategy
# ──────────────────────────────────────────

class TestParseTeachingStrategy:

    def test_parses_valid_strategy(self):
        plan = parse_teaching_strategy(TEACHER_STRATEGY)
        assert plan.total_levels == 3
        assert plan.max_auto_hints == 2

    def test_none_returns_empty_plan(self):
        assert parse_teaching_strategy(None).total_levels == 0

    def test_empty_dict_returns_empty_plan(self):
        assert parse_teaching_strategy({}).total_levels == 0

    def test_hints_not_a_list_returns_empty(self):
        assert parse_teaching_strategy({"hints": "壞掉的資料"}).total_levels == 0

    def test_skips_hints_with_empty_content(self):
        """教授存了半成品時，空白提示要跳過而不是顯示空白給學生"""
        plan = parse_teaching_strategy({"hints": [
            {"level": 1, "content": "有內容"},
            {"level": 2, "content": "   "},
            {"level": 3, "content": ""},
        ]})
        assert plan.total_levels == 1

    def test_invalid_trigger_falls_back_to_auto(self):
        plan = parse_teaching_strategy({"hints": [
            {"level": 1, "content": "x", "trigger": "亂填的"},
        ]})
        assert plan.hints[0]["trigger"] == TRIGGER_AUTO

    def test_missing_level_is_filled_by_order(self):
        plan = parse_teaching_strategy({"hints": [
            {"content": "第一"}, {"content": "第二"},
        ]})
        assert [h["level"] for h in plan.hints] == [1, 2]

    def test_hints_sorted_by_level(self):
        plan = parse_teaching_strategy({"hints": [
            {"level": 3, "content": "三"},
            {"level": 1, "content": "一"},
            {"level": 2, "content": "二"},
        ]})
        assert [h["content"] for h in plan.hints] == ["一", "二", "三"]

    def test_invalid_max_auto_hints_falls_back(self):
        plan = parse_teaching_strategy({"hints": [{"content": "x"}], "max_auto_hints": "abc"})
        assert plan.max_auto_hints == 2


# ──────────────────────────────────────────
# 解鎖規則（純邏輯）
# ──────────────────────────────────────────

class TestSelectHint:

    def setup_method(self):
        self.plan = parse_teaching_strategy(TEACHER_STRATEGY)

    def test_first_hint_given_automatically(self):
        hint = select_hint(self.plan, hints_used=0)
        assert hint["level"] == 1

    def test_second_hint_still_automatic(self):
        hint = select_hint(self.plan, hints_used=1)
        assert hint["level"] == 2

    def test_third_hint_requires_student_request(self):
        """第 3 層是 student_request，學生沒要就不給"""
        assert select_hint(self.plan, hints_used=2) is None

    def test_third_hint_given_when_requested(self):
        hint = select_hint(self.plan, hints_used=2, requested_by_student=True)
        assert hint["level"] == 3
        assert hint["is_example"] is True

    def test_returns_none_when_all_used(self):
        assert select_hint(self.plan, hints_used=3, requested_by_student=True) is None

    def test_auto_quota_limits_automatic_hints(self):
        """max_auto_hints=1 時，第二層自動不給，但學生主動要就給"""
        plan = parse_teaching_strategy({**TEACHER_STRATEGY, "max_auto_hints": 1})
        assert select_hint(plan, hints_used=1) is None
        assert select_hint(plan, hints_used=1, requested_by_student=True)["level"] == 2

    def test_empty_plan_returns_none(self):
        assert select_hint(HintPlan(), hints_used=0) is None


class TestWeakestDimension:

    def test_finds_lowest_ratio(self):
        assert weakest_dimension(DIMENSION_SCORES)["dimension"] == "功能性描述"

    def test_compares_by_ratio_not_raw_score(self):
        """2/10 比 1/4 差，即使原始分數比較高"""
        scores = [
            {"dimension": "A", "score": 2, "max_score": 10},
            {"dimension": "B", "score": 1, "max_score": 4},
        ]
        assert weakest_dimension(scores)["dimension"] == "A"

    def test_empty_returns_none(self):
        assert weakest_dimension([]) is None

    def test_ignores_zero_max_score(self):
        assert weakest_dimension([{"dimension": "A", "score": 0, "max_score": 0}]) is None


# ──────────────────────────────────────────
# 教授撰寫的提示
# ──────────────────────────────────────────

class TestTeacherHints:

    @pytest.mark.asyncio
    async def test_uses_teacher_content_and_marks_source(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage("你在哪個情境觀察到小明的行為？"))
        engine = HintEngine(llm=llm)

        hint = await engine.next_hint(
            teaching_strategy=TEACHER_STRATEGY,
            student_answer="小明有自閉症。",
            dimension_scores=DIMENSION_SCORES,
            hints_used=0,
        )

        assert hint.level == 1
        assert hint.source == SOURCE_TEACHER
        assert hint.trigger == TRIGGER_AUTO

    @pytest.mark.asyncio
    async def test_personalization_failure_falls_back_to_template(self):
        """個人化失敗時，學生仍要拿到老師原本的提示，不能是空白"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM 掛了"))
        engine = HintEngine(llm=llm)

        hint = await engine.next_hint(
            teaching_strategy=TEACHER_STRATEGY,
            student_answer="小明有自閉症。",
            dimension_scores=DIMENSION_SCORES,
            hints_used=0,
        )

        assert hint.content == "你觀察到哪個具體行為？"
        assert hint.source == SOURCE_TEACHER

    @pytest.mark.asyncio
    async def test_empty_personalization_falls_back_to_template(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage("   "))
        engine = HintEngine(llm=llm)

        hint = await engine.next_hint(
            teaching_strategy=TEACHER_STRATEGY,
            student_answer="作答",
            dimension_scores=DIMENSION_SCORES,
            hints_used=0,
        )
        assert hint.content == "你觀察到哪個具體行為？"

    @pytest.mark.asyncio
    async def test_example_hint_is_not_personalized(self):
        """範例型提示原樣呈現，不可被 AI 改寫"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage("被改寫過的內容"))
        engine = HintEngine(llm=llm)

        hint = await engine.next_hint(
            teaching_strategy=TEACHER_STRATEGY,
            student_answer="作答",
            dimension_scores=DIMENSION_SCORES,
            hints_used=2,
            requested_by_student=True,
        )

        assert hint.content == "例如：在自由遊戲…"
        assert llm.ainvoke.await_count == 0

    @pytest.mark.asyncio
    async def test_warn_copy_attaches_warning(self):
        engine = HintEngine(llm=AsyncMock())
        hint = await engine.next_hint(
            teaching_strategy=TEACHER_STRATEGY,
            student_answer="作答",
            dimension_scores=DIMENSION_SCORES,
            hints_used=2,
            requested_by_student=True,
        )
        assert hint.to_event_data()["warning"] == COPY_WARNING

    @pytest.mark.asyncio
    async def test_personalize_false_skips_llm(self):
        llm = AsyncMock()
        engine = HintEngine(llm=llm)
        hint = await engine.next_hint(
            teaching_strategy={**TEACHER_STRATEGY, "personalize": False},
            student_answer="作答",
            dimension_scores=DIMENSION_SCORES,
            hints_used=0,
        )
        assert hint.content == "你觀察到哪個具體行為？"
        assert llm.ainvoke.await_count == 0

    @pytest.mark.asyncio
    async def test_returns_none_when_exhausted(self):
        engine = HintEngine(llm=AsyncMock())
        hint = await engine.next_hint(
            teaching_strategy=TEACHER_STRATEGY,
            student_answer="作答",
            dimension_scores=DIMENSION_SCORES,
            hints_used=3,
            requested_by_student=True,
        )
        assert hint is None


# ──────────────────────────────────────────
# 教授未設定時的生成提示
# ──────────────────────────────────────────

class TestGeneratedHints:

    RUBRIC = {
        "dimensions": [
            {"name": "功能性描述", "weight": 40, "max_score": 4,
             "levels": [{"score": 4, "description": "完整"}, {"score": 1, "description": "未描述"}]},
        ]
    }

    @pytest.mark.asyncio
    async def test_generates_and_marks_source(self):
        """教授沒設定提示時，生成的內容必須標記 source=generated"""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=FakeMessage("小明在什麼情境下做了什麼？"))
        engine = HintEngine(llm=llm)

        hint = await engine.next_hint(
            teaching_strategy=None,
            student_answer="小明有自閉症。",
            dimension_scores=DIMENSION_SCORES,
            hints_used=0,
            rubric=self.RUBRIC,
        )

        assert hint.source == SOURCE_GENERATED
        assert hint.dimension == "功能性描述"
        assert hint.content == "小明在什麼情境下做了什麼？"

    @pytest.mark.asyncio
    async def test_no_scores_means_no_generated_hint(self):
        """沒有評分結果就生不出有意義的提示，寧可不給"""
        engine = HintEngine(llm=AsyncMock())
        hint = await engine.next_hint(
            teaching_strategy=None,
            student_answer="作答",
            dimension_scores=[],
            hints_used=0,
            rubric=self.RUBRIC,
        )
        assert hint is None

    @pytest.mark.asyncio
    async def test_generation_failure_returns_none(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("掛了"))
        engine = HintEngine(llm=llm)

        hint = await engine.next_hint(
            teaching_strategy=None,
            student_answer="作答",
            dimension_scores=DIMENSION_SCORES,
            hints_used=0,
            rubric=self.RUBRIC,
        )
        assert hint is None

    @pytest.mark.asyncio
    async def test_auto_quota_applies_to_generated_hints(self):
        engine = HintEngine(llm=AsyncMock())
        hint = await engine.next_hint(
            teaching_strategy=None,
            student_answer="作答",
            dimension_scores=DIMENSION_SCORES,
            hints_used=2,
            rubric=self.RUBRIC,
        )
        assert hint is None
