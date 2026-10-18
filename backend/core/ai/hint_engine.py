"""
分層提示引擎（Hint Engine）

對應需求：分層提示設計（引導問題 → 方向提示 → 局部範例）。

核心原則：**提示內容是教學設計，不是工程師的事。**
教授在工具建構器把提示寫進 `ai_tools.teaching_strategy`，
這裡只負責挑出「現在該給第幾層」以及「依學生的實際作答把它個人化」。

教授沒設定提示時，退而求其次依 Rubric 最弱構面即時生成，
並在事件裡標記 source="generated"，讓教授在後台看得出來哪些提示不是他寫的。
絕不回傳寫死的假提示假裝是教學設計。

`teaching_strategy` 的結構（對齊 docs/api.md 的 POST /api/tools/:id/hints）：

    {
      "hints": [
        {"level": 1, "trigger": "score_below_threshold",
         "content": "試想看看：你在哪個具體情境下，觀察到什麼行為？"},
        {"level": 2, "trigger": "student_request",
         "content": "功能性描述通常包含：情境、行為、頻率"},
        {"level": 3, "trigger": "student_request",
         "content": "例如：在自由遊戲時間，小明能獨立完成拼圖…",
         "is_example": true, "warn_copy": true}
      ],
      "max_auto_hints": 2,
      "personalize": true
    }
"""

import logging
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage, SystemMessage

from core.ai.llm_utils import invoke_with_retry

logger = logging.getLogger(__name__)

# 觸發方式
TRIGGER_AUTO = "score_below_threshold"   # 分數未達門檻時系統自動給
TRIGGER_REQUEST = "student_request"      # 學生主動按「我需要提示」才給
VALID_TRIGGERS = (TRIGGER_AUTO, TRIGGER_REQUEST)

# 來源
SOURCE_TEACHER = "teacher"
SOURCE_GENERATED = "generated"

# 教授沒指定時，自動提示最多給幾層（再往下就要學生主動要求）
DEFAULT_MAX_AUTO_HINTS = 2

COPY_WARNING = "這是參考範例，請用自己的話改寫成符合本案例的內容，不要直接複製。"

HINT_SYSTEM_PROMPT = """你是一位溫和的學前特殊教育教學引導者。

你的任務是把老師寫好的提示，依照這位學生的實際作答改寫得更貼近他的狀況。

嚴格遵守：
1. 不可以直接給出答案或示範完整寫法
2. 保留老師原本提示的教學意圖與層級深度，只是講得更貼近這份作答
3. 用引導的語氣，不要批評
4. 不超過 100 字
5. 只回傳提示文字本身，不要任何前綴、標題或引號"""

GENERATE_SYSTEM_PROMPT = """你是一位溫和的學前特殊教育教學引導者。

老師尚未為這個構面撰寫提示，請依評分規準生成一則引導提示。

嚴格遵守：
1. 不可以直接給出答案或示範完整寫法
2. 以問句引導學生自己發現缺漏
3. 扣住指定構面的評分規準
4. 不超過 100 字
5. 只回傳提示文字本身，不要任何前綴、標題或引號"""


@dataclass
class Hint:
    """一則要回傳給學生的提示"""
    level: int
    content: str
    trigger: str = TRIGGER_AUTO
    source: str = SOURCE_TEACHER
    is_example: bool = False
    warn_copy: bool = False
    dimension: str | None = None      # 針對哪個構面（生成型提示才有）

    def to_event_data(self) -> dict:
        data = {
            "level": self.level,
            "content": self.content,
            "trigger": self.trigger,
            "source": self.source,
            "is_example": self.is_example,
        }
        if self.warn_copy:
            data["warning"] = COPY_WARNING
        if self.dimension:
            data["dimension"] = self.dimension
        return data


@dataclass
class HintPlan:
    """把 teaching_strategy 解析成可用的提示計畫"""
    hints: list[dict] = field(default_factory=list)
    max_auto_hints: int = DEFAULT_MAX_AUTO_HINTS
    personalize: bool = True

    @property
    def total_levels(self) -> int:
        return len(self.hints)


def parse_teaching_strategy(teaching_strategy: dict | None) -> HintPlan:
    """
    把資料庫的 `ai_tools.teaching_strategy` 解析成 HintPlan。

    容錯：
      - 整包是 None / 空 dict → 空計畫（之後走生成路線）
      - hints 不是 list → 當成沒設定
      - 單則提示缺 level → 依順序補
      - trigger 不合法 → 退回 score_below_threshold
      - content 是空字串 → 該則跳過（教授存了半成品）
    """
    if not isinstance(teaching_strategy, dict):
        return HintPlan()

    raw_hints = teaching_strategy.get("hints")
    if not isinstance(raw_hints, list):
        return HintPlan()

    cleaned: list[dict] = []
    for i, item in enumerate(raw_hints, start=1):
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue

        trigger = item.get("trigger")
        if trigger not in VALID_TRIGGERS:
            trigger = TRIGGER_AUTO

        cleaned.append({
            "level": int(item.get("level") or i),
            "content": content,
            "trigger": trigger,
            "is_example": bool(item.get("is_example", False)),
            "warn_copy": bool(item.get("warn_copy", False)),
        })

    cleaned.sort(key=lambda h: h["level"])

    max_auto = teaching_strategy.get("max_auto_hints", DEFAULT_MAX_AUTO_HINTS)
    try:
        max_auto = int(max_auto)
    except (TypeError, ValueError):
        max_auto = DEFAULT_MAX_AUTO_HINTS

    return HintPlan(
        hints=cleaned,
        max_auto_hints=max_auto,
        personalize=bool(teaching_strategy.get("personalize", True)),
    )


def select_hint(
    plan: HintPlan,
    hints_used: int,
    requested_by_student: bool = False,
) -> dict | None:
    """
    決定「現在」該給哪一則提示。純邏輯，不呼叫 LLM，可完整單元測試。

    規則：
      - 已經給完所有層級 → None
      - 下一則是 student_request 但學生沒主動要 → None（等他自己按）
      - 下一則是 score_below_threshold 但自動額度已用完 → None
      - 學生主動要求時，不受 max_auto_hints 限制

    Args:
        plan:                 解析後的提示計畫
        hints_used:           這次嘗試之前已經給過幾則
        requested_by_student: 是否由學生主動要求
    Returns:
        下一則提示的 dict，或 None（代表此刻不該給提示）
    """
    if hints_used >= plan.total_levels:
        return None

    nxt = plan.hints[hints_used]

    if nxt["trigger"] == TRIGGER_REQUEST and not requested_by_student:
        return None

    if (
        nxt["trigger"] == TRIGGER_AUTO
        and not requested_by_student
        and hints_used >= plan.max_auto_hints
    ):
        return None

    return nxt


def weakest_dimension(dimension_scores: list[dict]) -> dict | None:
    """找出得分率最低的構面，提示要扣著它講才有意義。"""
    scored = [
        d for d in dimension_scores or []
        if float(d.get("max_score") or 0) > 0
    ]
    if not scored:
        return None
    return min(scored, key=lambda d: float(d.get("score", 0)) / float(d["max_score"]))


class HintEngine:
    """
    分層提示引擎。

    用法：
        engine = HintEngine()
        hint = await engine.next_hint(
            teaching_strategy=tool["teaching_strategy"],
            student_answer=answer,
            dimension_scores=scores,
            hints_used=2,
            requested_by_student=False,
            rubric=rubric_dict,
        )
    """

    def __init__(self, llm=None):
        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            # 延後 import：沒有要生成提示時不需要載入 LLM 套件
            from langchain_google_genai import ChatGoogleGenerativeAI
            from core.config import get_settings

            settings = get_settings()
            if not settings.gemini_api_key:
                raise ValueError("GEMINI_API_KEY 未設定")
            self._llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model,
                google_api_key=settings.gemini_api_key,
                temperature=0.5,   # 提示語句要自然，比評分高一些
            )
        return self._llm

    async def next_hint(
        self,
        *,
        teaching_strategy: dict | None,
        student_answer: str,
        dimension_scores: list[dict] | None = None,
        hints_used: int = 0,
        requested_by_student: bool = False,
        rubric: dict | None = None,
    ) -> Hint | None:
        """
        取得下一則提示。教授有設定就用他的，沒有就依 Rubric 生成。

        Returns:
            Hint，或 None（此刻不該給提示）
        """
        plan = parse_teaching_strategy(teaching_strategy)

        if plan.total_levels:
            selected = select_hint(plan, hints_used, requested_by_student)
            if selected is None:
                return None
            return await self._build_teacher_hint(
                selected, plan, student_answer, dimension_scores
            )

        # 教授沒設定提示 → 依 Rubric 生成，但仍受自動提示額度限制
        if not requested_by_student and hints_used >= DEFAULT_MAX_AUTO_HINTS:
            return None
        return await self._build_generated_hint(
            student_answer, dimension_scores, rubric, hints_used, requested_by_student
        )

    # ── 教授撰寫的提示 ────────────────────────────────────────────

    async def _build_teacher_hint(
        self,
        selected: dict,
        plan: HintPlan,
        student_answer: str,
        dimension_scores: list[dict] | None,
    ) -> Hint:
        content = selected["content"]

        # 範例型提示不做個人化：教授寫的範例就是要原樣呈現，
        # 改寫反而可能把「不要照抄」的示範變成一份可以照抄的答案
        should_personalize = plan.personalize and not selected["is_example"]

        if should_personalize:
            content = await self._personalize(
                template=content,
                student_answer=student_answer,
                dimension_scores=dimension_scores,
                fallback=content,
            )

        return Hint(
            level=selected["level"],
            content=content,
            trigger=selected["trigger"],
            source=SOURCE_TEACHER,
            is_example=selected["is_example"],
            warn_copy=selected["warn_copy"],
        )

    async def _personalize(
        self,
        *,
        template: str,
        student_answer: str,
        dimension_scores: list[dict] | None,
        fallback: str,
    ) -> str:
        """把老師的提示模板依學生作答改寫。失敗時原樣回傳模板。"""
        weakest = weakest_dimension(dimension_scores or [])
        weak_info = ""
        if weakest:
            weak_info = (
                f"這位學生在「{weakest.get('dimension', '')}」構面得分最低"
                f"（{weakest.get('score')}/{weakest.get('max_score')}），"
                f"理由：{weakest.get('reason', '')}"
            )

        prompt = f"""老師寫的提示模板：
{template}

{weak_info}

學生的作答：
{student_answer}

請把老師的提示改寫得更貼近這位學生的實際作答。"""

        try:
            content = await invoke_with_retry(
                self.llm,
                [
                    SystemMessage(content=HINT_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ],
                label="personalize_hint",
                max_attempts=2,     # 提示失敗可以退回模板，不值得重試三次
            )
            content = (content or "").strip()
            return content or fallback
        except Exception as e:  # noqa: BLE001
            logger.warning(f"提示個人化失敗，改用老師原始模板：{e}")
            return fallback

    # ── 教授未設定時的生成提示 ────────────────────────────────────

    async def _build_generated_hint(
        self,
        student_answer: str,
        dimension_scores: list[dict] | None,
        rubric: dict | None,
        hints_used: int,
        requested_by_student: bool,
    ) -> Hint | None:
        weakest = weakest_dimension(dimension_scores or [])

        # 連評分結果都沒有就無從生成（例如第一次作答還沒評分就要提示）
        if not weakest:
            return None

        dim_name = weakest.get("dimension", "")
        criteria = _find_dimension_criteria(rubric, dim_name)

        prompt = f"""【構面】{dim_name}

【評分規準】
{criteria}

【學生在這個構面的得分】{weakest.get('score')}/{weakest.get('max_score')}
【評分理由】{weakest.get('reason', '')}

【學生的作答】
{student_answer}

請生成一則引導提示，幫助學生自己發現這個構面還缺什麼。"""

        try:
            content = await invoke_with_retry(
                self.llm,
                [
                    SystemMessage(content=GENERATE_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ],
                label="generate_hint",
                max_attempts=2,
            )
            content = (content or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"提示生成失敗：{e}")
            return None

        if not content:
            return None

        return Hint(
            level=hints_used + 1,
            content=content,
            trigger=TRIGGER_REQUEST if requested_by_student else TRIGGER_AUTO,
            source=SOURCE_GENERATED,
            dimension=dim_name,
        )


def _find_dimension_criteria(rubric: dict | None, dim_name: str) -> str:
    """從標準化後的 rubric 取出指定構面的評分等級文字。"""
    for dim in (rubric or {}).get("dimensions", []):
        if dim.get("name") != dim_name:
            continue
        levels = sorted(
            dim.get("levels", []), key=lambda x: x["score"], reverse=True
        )
        if levels:
            return "\n".join(f"  {lv['score']}分：{lv['description']}" for lv in levels)
        return dim.get("description", "") or "（未提供規準說明）"
    return "（未提供規準說明）"
