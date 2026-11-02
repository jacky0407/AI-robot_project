"""
步驟解鎖：前面的「必修」步驟通過了，後面的步驟才能作答

規則（只看啟用中的步驟，依 step_order）：
  - 某一步之前，所有 is_required 的步驟都通過 → 解鎖
  - 選修步驟（is_required = false）沒通過不擋後面
  - 停用的步驟（is_active = false）不算、也不能作答
  - 教師端（owner / assistant）不受解鎖限制，方便試做

「通過」的判斷，教師判定優先於 AI（AI 評分永遠不覆蓋教師判定）：
  - 某次作答有「已發布、有給分」的教師複核 → 分數 >= 該步驟 pass_score 才算通過
    （老師可以把 AI 判不過的改成過，也可以把 AI 判過的改成不過）
  - 否則看 step_attempts.status == "passed"（AI 評分通過；老師要求重寫時會改成 revision_required）
  - 任何一次作答通過，這一步就算通過

後端用 service_role 不受 RLS 限制，所以送出作答時必須在這裡檢查，不能只靠前端把按鈕停用。
"""

from fastapi import HTTPException, status

from core.auth import CurrentUser

STEP_SELECT = "id, step_order, step_title, is_required, is_active, pass_score"
STEP_SELECT_BEFORE_010 = "id, step_order, step_title, is_required, pass_score"
ATTEMPT_SELECT = "step_id, status, teacher_reviews(final_score, is_published)"

# 與 schema.sql 的 module_steps.pass_score 預設值一致
DEFAULT_PASS_SCORE = 70


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def module_progress(db, user_id: str, module_id: str, *, bypass_locks: bool = False) -> list[dict]:
    """
    這位使用者在模組裡每個「啟用中」步驟的進度，依步驟順序。

    每一項：step_id、step_order、step_title、is_required、passed、locked、locked_by（擋住它的步驟名稱）
    """
    steps = load_active_steps(db, module_id)

    attempts_res = (
        db.table("step_attempts")
        .select(ATTEMPT_SELECT)
        .eq("user_id", user_id)
        .eq("module_id", module_id)
        .execute()
    )
    pass_score = {s["id"]: s.get("pass_score") or DEFAULT_PASS_SCORE for s in steps}
    passed_steps = {
        a.get("step_id")
        for a in attempts_res.data or []
        if a.get("step_id") in pass_score and attempt_passed(a, pass_score[a["step_id"]])
    }

    progress, blocker = [], None
    for s in steps:
        passed = s["id"] in passed_steps
        progress.append({
            "step_id": s["id"],
            "step_order": s.get("step_order"),
            "step_title": s.get("step_title") or "",
            "is_required": bool(s.get("is_required", True)),
            "passed": passed,
            "locked": blocker is not None and not bypass_locks,
            "locked_by": None if bypass_locks else blocker,
        })
        if blocker is None and s.get("is_required", True) and not passed:
            blocker = s.get("step_title") or f"步驟 {s.get('step_order')}"
    return progress


def load_active_steps(db, module_id: str) -> list[dict]:
    """模組裡啟用中的步驟，依 step_order 排序"""
    try:
        res = _select_steps(db, module_id, STEP_SELECT)
    except Exception:  # noqa: BLE001
        # 還沒跑 migration 010（沒有 is_active 欄位）：當作全部啟用，解鎖規則照常
        res = _select_steps(db, module_id, STEP_SELECT_BEFORE_010)
    rows = res.data if isinstance(res.data, list) else []
    steps = [s for s in rows if s.get("is_active", True)]
    return sorted(steps, key=lambda s: s.get("step_order") or 0)


def _select_steps(db, module_id: str, columns: str):
    return (
        db.table("module_steps")
        .select(columns)
        .eq("module_id", module_id)
        .order("step_order")
        .execute()
    )


def attempt_passed(attempt: dict, pass_score: int) -> bool:
    """單次作答是否通過；已發布且有給分的教師複核優先於 AI 結果。"""
    reviews = attempt.get("teacher_reviews") or []
    if isinstance(reviews, dict):
        reviews = [reviews]
    scored = [
        r for r in reviews
        if isinstance(r, dict) and r.get("is_published") and r.get("final_score") is not None
    ]
    if scored:
        return any(r["final_score"] >= pass_score for r in scored)
    return attempt.get("status") == "passed"


def check_step_unlocked(db, user: CurrentUser, step: dict) -> None:
    """
    送出作答前檢查。step 需含 id、module_id、is_active（submit 查的 module_steps 已帶出）。

    Raises:
        403 STEP_DISABLED：步驟已停用
        403 STEP_LOCKED：前面還有必修步驟沒通過
    """
    if user.is_staff:
        return

    if step.get("is_active") is False:
        raise _error(status.HTTP_403_FORBIDDEN, "STEP_DISABLED", "這個步驟已停用，請選擇其他步驟")

    for item in module_progress(db, user.id, step["module_id"]):
        if item["step_id"] == step["id"]:
            if item["locked"]:
                raise _error(
                    status.HTTP_403_FORBIDDEN,
                    "STEP_LOCKED",
                    f"請先通過「{item['locked_by']}」，這一步才會解鎖",
                )
            return
