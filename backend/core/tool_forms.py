"""
工具專屬表單與質性反思（#80，需求說明書第十二節，migration 029）

每支機器人可以有自己的幾份表單（前測、後測、反思、研究問卷），不和別的機器人共用。

題型（schema_json.questions[].type）：
    single 單選、multi 複選、scale 量尺（min～max 的整數）、short 簡答（200 字內）、long 長文字（5000 字內）
    每題可以設 required（必答）與 allow_na（可選「不適用」）

出現時點（trigger_timing）：
    pre_practice      練習前：這一關還沒填過就出現
    post_practice     結束後：這一關通過後出現一次
    after_feedback    看完回饋後：每次作答拿到回饋後出現
    post_revision     修正後：第 2 次以後的作答拿到回饋後出現
    every_n_attempts  每 N 次作答：第 N、2N… 次作答後出現
    teacher_release   教師發布：老師按「發布」後出現一次

前三種「一關一次」的（pre_practice、post_practice、teacher_release）以 學生＋關卡＋表單 判斷填過沒；
其他「跟著作答」的以 學生＋那一次作答＋表單 判斷。表單改版（新版本）不會讓填過的人再填一次。

必填與擋關（is_required、gate）：
    is_required = false → 學生可以略過（也記一筆 skipped = true）；true → 不能略過
    gate = completion    → 這份表單還沒「填答」（略過不算），這一關不算通過（core/step_unlock.py）
    gate = full_feedback → 還沒填答前，前端只顯示簡短回饋（回傳 feedback_locked 給前端）

版本：修改題目或設定 = 新增一列（同一個 form_key、version + 1），舊版 is_active = false；
舊的填答仍然指向舊版那一列，所以看得到當時的題目與選項。

這個檔只放純邏輯與資料存取，API 在 api/forms.py。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from core.privacy import detect_pii

logger = logging.getLogger(__name__)

QUESTION_TYPES = ("single", "multi", "scale", "short", "long")
TIMINGS = ("pre_practice", "post_practice", "after_feedback", "post_revision", "every_n_attempts", "teacher_release")
GATES = ("none", "completion", "full_feedback")
STEP_TIMINGS = ("pre_practice", "post_practice", "teacher_release")  # 一關一次
ATTEMPT_TIMINGS = ("after_feedback", "post_revision", "every_n_attempts")  # 跟著作答

TIMING_LABELS = {
    "pre_practice": "練習前",
    "post_practice": "結束後",
    "after_feedback": "看完回饋後",
    "post_revision": "修正後",
    "every_n_attempts": "每 N 次作答",
    "teacher_release": "教師發布",
}

MAX_QUESTIONS = 30
MAX_OPTIONS = 20
MAX_LABEL = 300
MAX_OPTION = 100
TEXT_LIMITS = {"short": 200, "long": 5000}
SCALE_MIN, SCALE_MAX = 0, 10
QUESTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

FORM_SELECT = (
    "id, form_key, tool_id, title, description, trigger_timing, trigger_every_n, released_at, "
    "is_required, gate, schema_json, version, is_active, created_by, created_at, updated_at"
)


class FormError(ValueError):
    """表單內容或填答不合規則；code 給前端判斷，message 直接顯示"""

    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# ── 題目 ─────────────────────────────────────────────────────────────────────


def normalize_schema(schema: Any) -> dict:
    """
    檢查並整理老師送來的題目，回傳要存進 schema_json 的內容：{"questions": [...]}。
    沒給 id 的題目自動編成 q1、q2…（跳過已用的）；題目 id 不能重複。
    """
    questions = schema.get("questions") if isinstance(schema, dict) else None
    if not isinstance(questions, list) or not questions:
        raise FormError("FORM_INVALID", "表單至少要有一題")
    if len(questions) > MAX_QUESTIONS:
        raise FormError("FORM_INVALID", f"一份表單最多 {MAX_QUESTIONS} 題")

    given = {q.get("id") for q in questions if isinstance(q, dict) and q.get("id")}
    used: set[str] = set()
    counter = 0
    result = []
    for index, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            raise FormError("FORM_INVALID", f"第 {index} 題格式不對")
        qid = q.get("id")
        if not qid:
            while True:
                counter += 1
                qid = f"q{counter}"
                if qid not in given and qid not in used:
                    break
        if not isinstance(qid, str) or not QUESTION_ID.match(qid):
            raise FormError("FORM_INVALID", f"第 {index} 題的編號只能用英數字、- 和 _（40 字內）")
        if qid in used:
            raise FormError("FORM_INVALID", f"題目編號「{qid}」重複了")
        used.add(qid)
        result.append(_normalize_question(q, qid, index))
    return {"questions": result}


def _normalize_question(q: dict, qid: str, index: int) -> dict:
    qtype = q.get("type")
    if qtype not in QUESTION_TYPES:
        raise FormError("FORM_INVALID", f"第 {index} 題的題型要是 單選、複選、量尺、簡答 或 長文字")
    label = str(q.get("label") or "").strip()
    if not label:
        raise FormError("FORM_INVALID", f"第 {index} 題沒有題目文字")
    if len(label) > MAX_LABEL:
        raise FormError("FORM_INVALID", f"第 {index} 題的題目太長（{MAX_LABEL} 字內）")

    out: dict[str, Any] = {
        "id": qid,
        "type": qtype,
        "label": label,
        "help": str(q.get("help") or "").strip()[:MAX_LABEL],
        "required": bool(q.get("required", False)),
        "allow_na": bool(q.get("allow_na", False)),
    }

    if qtype in ("single", "multi"):
        options = [str(o).strip() for o in q.get("options") or [] if str(o).strip()]
        if len(options) < 2:
            raise FormError("FORM_INVALID", f"第 {index} 題至少要有兩個選項")
        if len(options) > MAX_OPTIONS:
            raise FormError("FORM_INVALID", f"第 {index} 題最多 {MAX_OPTIONS} 個選項")
        if any(len(o) > MAX_OPTION for o in options):
            raise FormError("FORM_INVALID", f"第 {index} 題的選項太長（{MAX_OPTION} 字內）")
        if len(set(options)) != len(options):
            raise FormError("FORM_INVALID", f"第 {index} 題有重複的選項")
        out["options"] = options
    elif qtype == "scale":
        try:
            low, high = int(q.get("min", 1)), int(q.get("max", 5))
        except (TypeError, ValueError):
            raise FormError("FORM_INVALID", f"第 {index} 題的量尺範圍要是整數") from None
        if not (SCALE_MIN <= low < high <= SCALE_MAX):
            raise FormError("FORM_INVALID", f"第 {index} 題的量尺要在 {SCALE_MIN}～{SCALE_MAX} 之間，而且最小值小於最大值")
        out.update({
            "min": low,
            "max": high,
            "min_label": str(q.get("min_label") or "").strip()[:MAX_OPTION],
            "max_label": str(q.get("max_label") or "").strip()[:MAX_OPTION],
        })
    return out


def validate_settings(timing: str, every_n: int | None, gate: str) -> None:
    if timing not in TIMINGS:
        raise FormError("FORM_INVALID", "出現時點不正確")
    if gate not in GATES:
        raise FormError("FORM_INVALID", "填答規則不正確")
    if timing == "every_n_attempts" and (not isinstance(every_n, int) or every_n < 1):
        raise FormError("FORM_INVALID", "「每 N 次作答」要設定 N（1 以上）")


# ── 填答 ─────────────────────────────────────────────────────────────────────


def validate_answers(schema: dict, responses: Any) -> dict:
    """
    檢查學生的填答，回傳要存的內容 {"answers": {...}, "na": [...]}。
    只留表單裡有的題目；簡答與長文字要先過個資偵測（core/privacy.py，跟送出作答同一組規則）。
    """
    if not isinstance(responses, dict):
        raise FormError("RESPONSE_INVALID", "填答格式不對")
    raw_answers = responses.get("answers") if isinstance(responses.get("answers"), dict) else {}
    raw_na = responses.get("na") if isinstance(responses.get("na"), list) else []

    answers: dict[str, Any] = {}
    na: list[str] = []
    for q in schema.get("questions") or []:
        qid, label = q["id"], q["label"]
        if qid in raw_na:
            if not q.get("allow_na"):
                raise FormError("RESPONSE_INVALID", f"「{label}」不能選不適用", question_id=qid)
            na.append(qid)
            continue
        value = _check_answer(q, raw_answers.get(qid))
        if value is None:
            if q.get("required"):
                raise FormError("ANSWER_REQUIRED", f"「{label}」是必答題", question_id=qid)
            continue
        answers[qid] = value

    texts = [v for q in schema.get("questions") or [] if q["type"] in TEXT_LIMITS
             for v in [answers.get(q["id"])] if isinstance(v, str)]
    for text in texts:
        pii = detect_pii(text)
        if pii.has_risk:
            raise FormError("PII_DETECTED", pii.warning_message, detected_types=pii.detected_types)
    return {"answers": answers, "na": na}


def _check_answer(q: dict, value: Any) -> Any:
    """回傳整理後的答案；沒作答回 None"""
    label = q["label"]
    qtype = q["type"]
    if value is None or value == "" or value == []:
        return None
    if qtype == "single":
        if value not in q["options"]:
            raise FormError("RESPONSE_INVALID", f"「{label}」的選項不正確", question_id=q["id"])
        return value
    if qtype == "multi":
        if not isinstance(value, list) or any(v not in q["options"] for v in value):
            raise FormError("RESPONSE_INVALID", f"「{label}」的選項不正確", question_id=q["id"])
        # 依選項順序、去掉重複
        return [o for o in q["options"] if o in value]
    if qtype == "scale":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise FormError("RESPONSE_INVALID", f"「{label}」要選一個整數", question_id=q["id"])
        if not (q["min"] <= int(value) <= q["max"]):
            raise FormError("RESPONSE_INVALID", f"「{label}」要在 {q['min']}～{q['max']} 之間", question_id=q["id"])
        return int(value)
    # short / long
    if not isinstance(value, str):
        raise FormError("RESPONSE_INVALID", f"「{label}」要填文字", question_id=q["id"])
    text = value.strip()
    if not text:
        return None
    limit = TEXT_LIMITS[qtype]
    if len(text) > limit:
        raise FormError("RESPONSE_INVALID", f"「{label}」最多 {limit} 字", question_id=q["id"])
    return text


# ── 什麼時候該出現 ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepState:
    """這位學生在這一關的狀況（判斷表單該不該出現）"""

    latest_attempt_id: str | None = None
    latest_attempt_number: int = 0
    latest_has_feedback: bool = False  # 最新一次作答拿到回饋了（有評分或有文字回饋；只拿提示也算）
    step_passed: bool = False


def is_due(form: dict, state: StepState, answered: bool) -> bool:
    """
    這份表單現在該不該請學生填。answered = 這個時點已經填過（或略過）。
    """
    if answered:
        return False
    timing = form.get("trigger_timing")
    if timing == "pre_practice":
        return True
    if timing == "teacher_release":
        return bool(form.get("released_at"))
    if timing == "post_practice":
        return state.step_passed
    if not state.latest_attempt_id or not state.latest_has_feedback:
        return False
    if timing == "after_feedback":
        return True
    if timing == "post_revision":
        return state.latest_attempt_number >= 2
    if timing == "every_n_attempts":
        n = form.get("trigger_every_n") or 0
        return n >= 1 and state.latest_attempt_number % n == 0
    return False


def slot_answered(form: dict, responses: list[dict], state: StepState) -> bool:
    """這個時點已經有填答（或略過）。responses = 這位學生在這一關、同一個 form_key 的所有填答"""
    if form.get("trigger_timing") in STEP_TIMINGS:
        return bool(responses)
    return any(r.get("attempt_id") == state.latest_attempt_id for r in responses) if state.latest_attempt_id else False


def blocks_completion(form: dict, responses: list[dict]) -> bool:
    """gate = completion 的表單，這一關要「填答」（不是略過）過才算通過"""
    if form.get("gate") != "completion":
        return False
    return not any(not r.get("skipped") for r in responses)


# ── 資料存取 ─────────────────────────────────────────────────────────────────


def _rows(res) -> list[dict]:
    data = getattr(res, "data", None)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def active_forms(db, tool_id: str) -> list[dict]:
    """這支機器人目前啟用中的表單（每個 form_key 最新一版），依建立時間"""
    try:
        rows = _rows(db.table("forms").select(FORM_SELECT).eq("tool_id", tool_id).eq("is_active", True).execute())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取表單失敗（是否尚未執行 migration 029？）：{e}")
        return []
    return sorted(rows, key=lambda f: f.get("created_at") or "")


def user_step_responses(db, user_id: str, step_id: str) -> dict[str, list[dict]]:
    """這位學生在這一關的所有填答，依 form_key 分組"""
    try:
        rows = _rows(
            db.table("form_responses").select("id, form_key, attempt_id, skipped, submitted_at")
            .eq("user_id", user_id).eq("step_id", step_id).execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取表單填答失敗（是否尚未執行 migration 029？）：{e}")
        return {}
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r.get("form_key"), []).append(r)
    return grouped


def steps_blocked_by_forms(db, user_id: str, steps: list[dict]) -> set[str]:
    """
    哪些關卡還有 gate = completion 的表單沒填（這些關卡就算 AI 判通過，也還不算通過）。
    steps 要有 id、tool_id。讀不到表單（還沒跑 migration 029）就當作沒有。
    """
    tool_ids = sorted({s.get("tool_id") for s in steps if isinstance(s.get("tool_id"), str)})
    if not tool_ids:
        return set()
    try:
        forms = _rows(
            db.table("forms").select("form_key, tool_id, gate").in_("tool_id", tool_ids)
            .eq("is_active", True).eq("gate", "completion").execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取表單失敗（是否尚未執行 migration 029？）：{e}")
        return set()
    if not forms:
        return set()
    step_ids = [s["id"] for s in steps if s.get("tool_id")]
    try:
        rows = _rows(
            db.table("form_responses").select("form_key, step_id, skipped")
            .eq("user_id", user_id).in_("step_id", step_ids).execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取表單填答失敗：{e}")
        rows = []
    filled = {(r.get("step_id"), r.get("form_key")) for r in rows if not r.get("skipped")}
    by_tool: dict[str, list[str]] = {}
    for f in forms:
        by_tool.setdefault(f.get("tool_id"), []).append(f.get("form_key"))
    return {
        s["id"] for s in steps
        if any((s["id"], key) not in filled for key in by_tool.get(s.get("tool_id"), []))
    }


def public_form(form: dict) -> dict:
    """給學生看的表單內容（不含建立者等後台欄位）"""
    return {
        "id": form.get("id"),
        "form_key": form.get("form_key"),
        "title": form.get("title"),
        "description": form.get("description") or "",
        "version": form.get("version"),
        "trigger_timing": form.get("trigger_timing"),
        "timing_label": TIMING_LABELS.get(form.get("trigger_timing"), ""),
        "is_required": bool(form.get("is_required")),
        "gate": form.get("gate") or "none",
        "questions": (form.get("schema_json") or {}).get("questions") or [],
    }
