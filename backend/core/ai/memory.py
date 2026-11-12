"""
機器人記憶（Bot Memory）

每支機器人（= 模組裡的一個步驟）對同一位學生記得兩件事：
  1. 這一步的作答歷程：前幾次的分數、最弱構面、重複犯的錯、AI 回饋、
     已給過的提示，以及教師已發布的複核意見（以教師為準）
  2. 前面步驟的成果：同一模組中排在前面的步驟，學生最新的作答
     （有通過的版本優先），讓後面的機器人知道前面寫了什麼

記憶完全從既有資料表組出來（step_attempts、ai_evaluations、teacher_reviews、
prompt_logs），不新增資料表；所有機器人共用同一套邏輯。

設計原則：
  - **記憶不影響逐構面評分**：分數只看這次作答對照 Rubric，
    記憶只用在整體回饋與提示，避免「上次低分、這次也偏低」的錨定效應
  - **記憶不增加 LLM 呼叫次數**：只是把文字加進原本就會送出的 prompt
  - **讀取失敗不擋評分**：記憶是輔助資訊，查不到就當作沒有記憶

教授可以在 `ai_tools.teaching_strategy.memory` 逐支機器人調整：

    {
      "memory": {
        "enabled": true,              // 關掉就完全不讀記憶
        "max_attempts": 3,            // 記住這一步最近幾次作答
        "include_prior_steps": true   // 是否讀前面步驟的成果
      }
    }
"""

import logging
from collections import Counter
from dataclasses import dataclass, field

from core.step_unlock import DEFAULT_PASS_SCORE, attempt_passed

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
MAX_ATTEMPTS_LIMIT = 10           # 再多 prompt 會太長，教授填更大也夾回來

# 「重複出現的錯誤」看最近幾次作答。比 max_attempts 大：只給提示、轉介老師的作答
# 沒有評分，會把有錯誤的作答擠出最近 3 次的視窗（2026-11-02 測試報告 #4）
RECURRING_WINDOW = 10

# 各段文字截斷長度：記憶是背景資訊，不能蓋過這次作答本身
PRIOR_STEP_CHARS = 400
FEEDBACK_CHARS = 150
HINT_CHARS = 80

# 這一步的歷程：評分、錯誤、提示、已發布的教師複核
ATTEMPT_SELECT = (
    "id, attempt_number, status, "
    "ai_evaluations(total_score, dimension_scores, detected_errors, feedback_text), "
    "teacher_reviews(final_score, final_feedback, is_published), "
    "prompt_logs(hint_level, hint_content)"
)

# 前面步驟的成果：同模組每一步的作答與步驟順序
# 「是否通過」與步驟解鎖用同一套規則（core/step_unlock.attempt_passed）：
# 已發布的教師分數優先於 AI，所以要一起讀 teacher_reviews 與門檻 pass_score
PRIOR_STEP_SELECT = (
    "step_id, attempt_number, status, user_input_content, "
    "module_steps(step_order, step_title, pass_score), "
    "teacher_reviews(final_score, is_published)"
)


@dataclass
class MemoryConfig:
    enabled: bool = True
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    include_prior_steps: bool = True


@dataclass
class AttemptMemory:
    """這一步的某一次作答"""
    attempt_number: int
    score: float | None = None               # 0–100 加權總分；只拿到提示沒評分時為 None
    weakest_dimension: str | None = None
    errors: list[str] = field(default_factory=list)   # 錯誤分類的 label
    feedback: str = ""
    hints: list[str] = field(default_factory=list)
    teacher_score: int | None = None          # 只收已發布的教師複核
    teacher_feedback: str = ""


@dataclass
class PriorStepMemory:
    """前面某一步學生最新（通過優先）的作答"""
    step_order: int
    step_title: str
    content: str
    passed: bool


@dataclass
class BotMemory:
    attempts: list[AttemptMemory] = field(default_factory=list)       # 舊 → 新
    prior_steps: list[PriorStepMemory] = field(default_factory=list)  # 依步驟順序
    # 最近 RECURRING_WINDOW 次作答各自的錯誤；None 表示沒另外讀，改用 attempts
    error_history: list[list[str]] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.attempts and not self.prior_steps

    def recurring_errors(self) -> list[str]:
        """在兩次以上作答都出現的錯誤，依出現次數排序"""
        history = self.error_history
        if history is None:
            history = [a.errors for a in self.attempts]
        counts = Counter(label for errors in history for label in set(errors))
        return [label for label, n in counts.most_common() if n >= 2]

    def to_prompt_text(self) -> str:
        """組成放進 prompt 的記憶段落；沒有記憶時回空字串。"""
        if self.is_empty:
            return ""

        lines: list[str] = []

        if self.attempts:
            lines.append("【這位學生在本步驟的先前作答】")
            for a in self.attempts:
                score = f"{a.score:.0f} 分" if a.score is not None else "未評分（只給了提示）"
                parts = [f"第 {a.attempt_number} 次：{score}"]
                if a.weakest_dimension:
                    parts.append(f"最弱構面「{a.weakest_dimension}」")
                if a.errors:
                    parts.append("錯誤：" + "、".join(a.errors))
                lines.append("- " + "，".join(parts))
                if a.feedback:
                    lines.append(f"  AI 回饋：{a.feedback}")
                for hint in a.hints:
                    lines.append(f"  已給過的提示：{hint}")
                if a.teacher_feedback or a.teacher_score is not None:
                    teacher = f"{a.teacher_score} 分" if a.teacher_score is not None else ""
                    lines.append(
                        f"  教師複核（以此為準）：{teacher} {a.teacher_feedback}".rstrip()
                    )

            recurring = self.recurring_errors()
            if recurring:
                lines.append("重複出現的錯誤：" + "、".join(recurring))

        if self.prior_steps:
            if lines:
                lines.append("")
            lines.append("【這位學生在前面步驟的成果】")
            for p in self.prior_steps:
                status = "已通過" if p.passed else "尚未通過"
                lines.append(f"- 步驟 {p.step_order}「{p.step_title}」（{status}）：{p.content}")

        return "\n".join(lines)

    def to_event_data(self) -> dict:
        """給前端或教師後台看的摘要（不含前面步驟的全文）"""
        return {
            "attempts": [
                {
                    "attempt_number": a.attempt_number,
                    "score": a.score,
                    "weakest_dimension": a.weakest_dimension,
                    "errors": a.errors,
                    "hints_given": len(a.hints),
                    "teacher_score": a.teacher_score,
                }
                for a in self.attempts
            ],
            "recurring_errors": self.recurring_errors(),
            "prior_steps": [
                {"step_order": p.step_order, "step_title": p.step_title, "passed": p.passed}
                for p in self.prior_steps
            ],
        }


def parse_memory_config(teaching_strategy: dict | None) -> MemoryConfig:
    """
    從 `ai_tools.teaching_strategy.memory` 讀設定。

    沒設定 → 預設開啟；格式不對的欄位一律用預設值，不讓評分壞掉。
    """
    raw = (teaching_strategy or {}).get("memory") if isinstance(teaching_strategy, dict) else None
    if not isinstance(raw, dict):
        return MemoryConfig()

    config = MemoryConfig()
    if isinstance(raw.get("enabled"), bool):
        config.enabled = raw["enabled"]
    if isinstance(raw.get("include_prior_steps"), bool):
        config.include_prior_steps = raw["include_prior_steps"]

    max_attempts = raw.get("max_attempts")
    if isinstance(max_attempts, int) and not isinstance(max_attempts, bool):
        config.max_attempts = max(0, min(max_attempts, MAX_ATTEMPTS_LIMIT))

    return config


def load_bot_memory(db, user_id: str, step_row: dict) -> BotMemory:
    """
    讀取這位學生在這支機器人的記憶。

    Args:
        db:       Supabase client
        user_id:  一律用 token 驗證過的使用者 id
        step_row: module_steps 的資料列，需含 id、module_id、step_order 與 ai_tools.teaching_strategy

    任何查詢失敗都回傳空記憶，只記 log。
    """
    tool = step_row.get("ai_tools") or {}
    config = parse_memory_config(tool.get("teaching_strategy"))
    if not config.enabled:
        return BotMemory()

    memory = BotMemory()

    try:
        if config.max_attempts:
            recent = _load_attempts(db, user_id, step_row["id"], RECURRING_WINDOW)
            memory.attempts = recent[-config.max_attempts:]
            memory.error_history = [a.errors for a in recent]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取本步驟記憶失敗，改為無記憶：{e}")

    try:
        step_order = step_row.get("step_order")
        if config.include_prior_steps and isinstance(step_order, int) and step_order > 1:
            memory.prior_steps = _load_prior_steps(
                db, user_id, step_row.get("module_id"), step_order
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取前面步驟成果失敗，改為無記憶：{e}")

    return memory


# ──────────────────────────────────────────
# 內部函式
# ──────────────────────────────────────────

def _load_attempts(db, user_id: str, step_id: str, max_attempts: int) -> list[AttemptMemory]:
    res = (
        db.table("step_attempts")
        .select(ATTEMPT_SELECT)
        .eq("user_id", user_id)
        .eq("step_id", step_id)
        .order("attempt_number", desc=True)
        .limit(max_attempts)
        .execute()
    )
    rows = res.data if isinstance(res.data, list) else []
    attempts = [_attempt_from_row(r) for r in rows[:max_attempts] if isinstance(r, dict)]
    attempts.sort(key=lambda a: a.attempt_number)
    return attempts


def _attempt_from_row(row: dict) -> AttemptMemory:
    evaluation = _first(row.get("ai_evaluations"))
    memory = AttemptMemory(attempt_number=int(row.get("attempt_number") or 0))

    if evaluation:
        if evaluation.get("total_score") is not None:
            memory.score = float(evaluation["total_score"])
        memory.weakest_dimension = _weakest_dimension_name(evaluation.get("dimension_scores"))
        memory.errors = [
            str(e.get("label") or e.get("code"))
            for e in (evaluation.get("detected_errors") or [])
            if isinstance(e, dict) and (e.get("label") or e.get("code"))
        ]
        memory.feedback = _truncate(evaluation.get("feedback_text"), FEEDBACK_CHARS)

    logs = row.get("prompt_logs") or []
    if isinstance(logs, list):
        logs = sorted(
            (log for log in logs if isinstance(log, dict)),
            key=lambda log: log.get("hint_level") or 0,
        )
        memory.hints = [
            _truncate(log.get("hint_content"), HINT_CHARS)
            for log in logs
            if log.get("hint_content")
        ]

    # 學生還沒看到的教師意見不放進記憶，避免 AI 搶先說出老師還沒發布的判定
    review = next(
        (r for r in _as_list(row.get("teacher_reviews")) if r.get("is_published")),
        None,
    )
    if review:
        memory.teacher_score = review.get("final_score")
        memory.teacher_feedback = _truncate(review.get("final_feedback"), FEEDBACK_CHARS)

    return memory


def _load_prior_steps(db, user_id: str, module_id: str | None, step_order: int) -> list[PriorStepMemory]:
    if not module_id:
        return []

    res = (
        db.table("step_attempts")
        .select(PRIOR_STEP_SELECT)
        .eq("user_id", user_id)
        .eq("module_id", module_id)
        .order("attempt_number", desc=True)
        .execute()
    )
    rows = res.data if isinstance(res.data, list) else []

    # 每一步取一筆：有通過的版本優先，否則取最新一次
    best: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        step = _first(row.get("module_steps")) or {}
        order = step.get("step_order")
        if not isinstance(order, int) or order >= step_order:
            continue
        current = best.get(row.get("step_id"))
        if current is None or _prefer(row, current):
            best[row.get("step_id")] = row

    prior = []
    for row in best.values():
        step = _first(row.get("module_steps")) or {}
        prior.append(PriorStepMemory(
            step_order=step["step_order"],
            step_title=step.get("step_title") or "",
            content=_truncate(row.get("user_input_content"), PRIOR_STEP_CHARS),
            passed=_row_passed(row),
        ))
    prior.sort(key=lambda p: p.step_order)
    return prior


def _row_passed(row: dict) -> bool:
    """
    這次作答是否通過，規則同步驟解鎖：已發布且有給分的教師判定優先，否則看 AI 的 status。
    （2026-11-12：原本只看 status，老師改判通過的關卡在記憶裡仍顯示「未通過」）
    """
    step = _first(row.get("module_steps")) or {}
    return attempt_passed(row, step.get("pass_score") or DEFAULT_PASS_SCORE)


def _prefer(candidate: dict, current: dict) -> bool:
    """candidate 是否比 current 更適合當這一步的代表作答"""
    cand_passed = _row_passed(candidate)
    curr_passed = _row_passed(current)
    if cand_passed != curr_passed:
        return cand_passed
    return (candidate.get("attempt_number") or 0) > (current.get("attempt_number") or 0)


def _weakest_dimension_name(dimension_scores) -> str | None:
    """得分率最低的構面名稱"""
    best_name, best_ratio = None, None
    for d in dimension_scores or []:
        if not isinstance(d, dict):
            continue
        try:
            ratio = float(d.get("score", 0)) / float(d.get("max_score") or 4)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if best_ratio is None or ratio < best_ratio:
            best_name, best_ratio = d.get("dimension"), ratio
    return best_name


def _as_list(value) -> list[dict]:
    """PostgREST 的巢狀關聯可能回 list、單一 dict 或 None"""
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _first(value) -> dict | None:
    items = _as_list(value)
    return items[0] if items else None


def _truncate(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"
