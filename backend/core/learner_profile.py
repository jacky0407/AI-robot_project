"""
學習者側寫（Learner Profile）— 機器人記憶系統改版提案・階段 1

每位學生 × 每個模組一份側寫，由程式從既有資料表計算，不呼叫 LLM：
  - dimensions：各評分構面的熟練度（得分率的指數加權平均，越新的作答權重越高）與趨勢
  - errors：常犯錯誤的累計次數、出現在哪些步驟、最後出現時間；
            在「會檢查這個錯誤的步驟」連續 FORGET_AFTER 次沒再出現 → 視為已改善（遺忘）
  - steps：每一步的作答次數、最佳分數、是否通過（教師判定優先，規則同步驟解鎖）
  - teacher_notes：最近幾則已發布的教師意見

設計原則（同 core/ai/memory.py）：
  - 側寫不影響評分：只用在整體回饋、提示與教師概述
  - 只存數字、錯誤代碼與名稱、教師已發布的意見；不存學生原文與姓名
  - 每次更新新增一列（version + 1），不覆蓋舊版本
  - 任何失敗只記 log，不影響評分流程（還沒跑 migration 015 時也一樣）

為什麼每次「從頭重算」而不是增量更新：
  一位學生在一個模組只有幾十筆作答，重算很便宜；
  重算的結果只由資料決定，不會因為某次更新失敗或順序錯亂而越算越偏。
"""

import logging
from datetime import datetime, timezone

from core.step_unlock import DEFAULT_PASS_SCORE, attempt_passed

logger = logging.getLogger(__name__)

PROFILE_SCHEMA_VERSION = 1

# 熟練度的指數加權：新一次作答占 50%
MASTERY_ALPHA = 0.5
# 趨勢：和上一次相比變化超過 5 個百分點才算進步／退步
TREND_EPSILON = 0.05
# 遺忘：在會檢查這個錯誤的步驟，連續 3 次評分都沒再出現 → 視為已改善
FORGET_AFTER = 3
# 保留最近幾則已發布的教師意見
TEACHER_NOTES_KEEP = 3
TEACHER_NOTE_CHARS = 150

ATTEMPT_SELECT = (
    "id, step_id, attempt_number, status, created_at, "
    "module_steps(step_order, step_title, pass_score), "
    "ai_evaluations(total_score, dimension_scores, detected_errors), "
    "teacher_reviews(final_score, final_feedback, is_published, reviewed_at)"
)


# ──────────────────────────────────────────
# 計算（純函式）
# ──────────────────────────────────────────

def build_profile(attempts: list[dict]) -> dict:
    """
    從一位學生在一個模組的所有作答算出側寫。

    attempts：step_attempts 的資料列（含 module_steps、ai_evaluations、teacher_reviews），順序不拘。
    """
    ordered = sorted(
        (a for a in attempts if isinstance(a, dict)),
        key=lambda a: (a.get("created_at") or "", a.get("attempt_number") or 0),
    )

    dims: dict[str, dict] = {}
    errors: dict[str, dict] = {}
    steps: dict[str, dict] = {}
    notes: list[dict] = []
    evaluated = 0

    for a in ordered:
        step = _first(a.get("module_steps")) or {}
        step_id = a.get("step_id")
        order = step.get("step_order")
        at = a.get("created_at")
        pass_score = step.get("pass_score") or DEFAULT_PASS_SCORE

        s = steps.setdefault(step_id, {
            "step_id": step_id, "step_order": order, "step_title": step.get("step_title") or "",
            "attempts": 0, "evaluated": 0, "best_score": None, "latest_score": None, "passed": False,
        })
        s["attempts"] += 1
        if attempt_passed(a, pass_score):
            s["passed"] = True

        for r in _as_list(a.get("teacher_reviews")):
            if r.get("is_published") and (r.get("final_feedback") or r.get("final_score") is not None):
                notes.append({
                    "step_order": order,
                    "final_score": r.get("final_score"),
                    "feedback": _truncate(r.get("final_feedback"), TEACHER_NOTE_CHARS),
                    "reviewed_at": r.get("reviewed_at"),
                })

        evaluation = _first(a.get("ai_evaluations"))
        if not evaluation:
            continue  # 只拿到提示、轉介老師：沒有評分，不影響熟練度與錯誤統計
        evaluated += 1
        s["evaluated"] += 1
        score = evaluation.get("total_score")
        if score is not None:
            s["latest_score"] = score
            s["best_score"] = score if s["best_score"] is None else max(s["best_score"], score)

        for d in evaluation.get("dimension_scores") or []:
            ratio = _ratio(d)
            name = d.get("dimension") if isinstance(d, dict) else None
            if ratio is None or not name:
                continue
            entry = dims.get(name)
            if entry is None:
                dims[name] = {"name": name, "mastery": ratio, "latest": ratio, "previous": None,
                              "evaluations": 1, "step_orders": [order]}
            else:
                entry["previous"] = entry["latest"]
                entry["latest"] = ratio
                entry["mastery"] = MASTERY_ALPHA * ratio + (1 - MASTERY_ALPHA) * entry["mastery"]
                entry["evaluations"] += 1
                if order not in entry["step_orders"]:
                    entry["step_orders"].append(order)

        seen = {}
        for e in evaluation.get("detected_errors") or []:
            if not isinstance(e, dict):
                continue
            code = str(e.get("code") or e.get("label") or "").strip()
            if code:
                seen[code] = str(e.get("label") or code)

        for code, label in seen.items():
            entry = errors.setdefault(code, {
                "code": code, "label": label, "count": 0, "step_ids": [], "step_orders": [],
                "first_seen_at": at, "last_seen_at": at, "absent_streak": 0, "improved_at": None,
            })
            entry["count"] += 1
            entry["label"] = label
            entry["last_seen_at"] = at
            entry["absent_streak"] = 0
            entry["improved_at"] = None  # 又出現了：重新算
            if step_id not in entry["step_ids"]:
                entry["step_ids"].append(step_id)
                entry["step_orders"].append(order)

        # 只在「會檢查這個錯誤」的步驟（曾在這一步出現過）算缺席，
        # 否則換到不檢查這個錯誤的步驟，會被誤判成「已改善」
        for code, entry in errors.items():
            if code in seen or step_id not in entry["step_ids"]:
                continue
            entry["absent_streak"] += 1
            if entry["absent_streak"] == FORGET_AFTER:
                entry["improved_at"] = at

    error_list = []
    for entry in errors.values():
        active = entry["absent_streak"] < FORGET_AFTER
        error_list.append({
            "code": entry["code"],
            "label": entry["label"],
            "count": entry["count"],
            "step_orders": entry["step_orders"],
            "first_seen_at": entry["first_seen_at"],
            "last_seen_at": entry["last_seen_at"],
            "absent_streak": entry["absent_streak"],
            "active": active,
            "improved_at": None if active else entry["improved_at"],
        })
    error_list.sort(key=lambda e: (not e["active"], -e["count"], e["label"]))

    dim_list = []
    for entry in dims.values():
        dim_list.append({
            "name": entry["name"],
            "mastery": round(entry["mastery"], 3),
            "latest": round(entry["latest"], 3),
            "trend": _trend(entry["latest"], entry["previous"]),
            "evaluations": entry["evaluations"],
            "step_orders": entry["step_orders"],
        })
    dim_list.sort(key=lambda d: d["mastery"])

    step_list = sorted(steps.values(), key=lambda s: (s["step_order"] is None, s["step_order"] or 0))
    notes.sort(key=lambda n: n.get("reviewed_at") or "")

    return {
        "schema": PROFILE_SCHEMA_VERSION,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "attempt_count": len(ordered),
        "evaluated_count": evaluated,
        "dimensions": dim_list,
        "errors": error_list,
        "steps": step_list,
        "teacher_notes": notes[-TEACHER_NOTES_KEEP:],
    }


def profile_prompt_lines(profile: dict | None, current_step_order: int | None = None) -> list[str]:
    """
    給 AI 助教看的側寫重點（加進 core/ai/memory.py 的記憶段落）。

    只放兩件事，避免蓋過這次作答本身：
      - 仍常犯的錯誤（出現兩次以上，或出現在其他步驟）——這是「跨步驟帶入」
      - 整個模組最弱的兩個構面（熟練度低於 60%）
    """
    if not isinstance(profile, dict):
        return []
    lines: list[str] = []

    recurring = []
    for e in profile.get("errors") or []:
        if not e.get("active"):
            continue
        other_steps = [o for o in e.get("step_orders") or [] if o is not None and o != current_step_order]
        if e.get("count", 0) >= 2 or other_steps:
            where = "、".join(f"步驟 {o}" for o in (e.get("step_orders") or []) if o is not None)
            recurring.append(f"{e.get('label')}（{e.get('count')} 次{f'，{where}' if where else ''}）")
    if recurring:
        lines.append("本模組仍常犯的錯誤：" + "；".join(recurring[:3]))

    weak = [d for d in profile.get("dimensions") or [] if (d.get("mastery") or 0) < 0.6][:2]
    if weak:
        lines.append("本模組較弱的構面：" + "、".join(f"{d['name']}（約 {round(d['mastery'] * 100)}%）" for d in weak))

    improved = [e.get("label") for e in profile.get("errors") or [] if not e.get("active")]
    if improved:
        lines.append("已改善、可給予肯定的錯誤：" + "、".join(improved[:3]))
    return lines


# ──────────────────────────────────────────
# 讀寫資料庫
# ──────────────────────────────────────────

def load_latest_profile(db, user_id: str, module_id: str | None) -> dict | None:
    """最新版本的側寫列（含 version、profile、created_at）；沒有或讀取失敗回 None。"""
    if not module_id:
        return None
    try:
        res = (
            db.table("learner_profiles")
            .select("version, profile, created_at, source_attempt_id")
            .eq("user_id", user_id)
            .eq("module_id", module_id)
            .order("version", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:  # noqa: BLE001 - 還沒跑 migration 015 等狀況
        logger.warning(f"讀取學習者側寫失敗（請確認已執行 migration 015）：{e}")
        return None
    rows = res.data if isinstance(res.data, list) else []
    return rows[0] if rows and isinstance(rows[0], dict) else None


def refresh_learner_profile(db, user_id: str, module_id: str | None, source_attempt_id: str | None = None) -> dict | None:
    """
    重新計算並新增一個版本。評分完成、教師判定後呼叫。

    回傳新的 profile（dict）；失敗回 None，只記 log，不往外拋。
    """
    if not module_id:
        return None
    try:
        res = (
            db.table("step_attempts")
            .select(ATTEMPT_SELECT)
            .eq("user_id", user_id)
            .eq("module_id", module_id)
            .execute()
        )
        attempts = res.data if isinstance(res.data, list) else []
        profile = build_profile(attempts)

        latest = load_latest_profile(db, user_id, module_id)
        version = (latest or {}).get("version") or 0
        db.table("learner_profiles").insert({
            "user_id": user_id,
            "module_id": module_id,
            "version": version + 1,
            "profile": profile,
            "source_attempt_id": source_attempt_id,
        }).execute()
        return profile
    except Exception as e:  # noqa: BLE001 - 側寫是輔助資訊，不能擋評分
        logger.warning(f"更新學習者側寫失敗（請確認已執行 migration 015）：{e}")
        return None


# ──────────────────────────────────────────
# 內部函式
# ──────────────────────────────────────────

def _ratio(d) -> float | None:
    if not isinstance(d, dict) or d.get("failed"):
        return None  # 評分失敗給的保底分不算進熟練度
    try:
        max_score = float(d.get("max_score") or 4)
        return max(0.0, min(float(d.get("score", 0)) / max_score, 1.0))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _trend(latest: float, previous: float | None) -> str:
    if previous is None:
        return "new"
    if latest - previous > TREND_EPSILON:
        return "up"
    if previous - latest > TREND_EPSILON:
        return "down"
    return "flat"


def _as_list(value) -> list[dict]:
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
