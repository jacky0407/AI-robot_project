"""
關卡「怎麼算通過」（module_steps.pass_mode，migration 019；#65）

  score          AI（或教師）分數 >= pass_score 才通過（原本的做法、預設值）
  complete       送出作答即通過；機器人有評分規準時仍會評分給學生參考
  teacher_review 教師複核後才通過；AI 結果只給學生參考

是否通過最後一律寫在 step_attempts.status（passed / revision_required / submitted），
步驟解鎖、記憶、側寫、監控都讀 status 與已發布的教師分數（core/step_unlock.attempt_passed），
所以不同的通過方式只要在「送出作答」與「教師判定」兩個時間點決定 status，其他地方不用改。

AI 評分永遠不覆蓋教師判定：教師判定之後才決定的 status，送出作答時不會再改。
"""

from typing import Optional

PASS_MODES = ("score", "complete", "teacher_review")
DEFAULT_PASS_MODE = "score"


def has_rubric(rubric_criteria) -> bool:
    """機器人有沒有可用的評分規準（至少一個有名稱的評分項目）"""
    if not isinstance(rubric_criteria, list):
        return False
    return any(
        isinstance(item, dict) and str(item.get("dimension") or item.get("name") or "").strip()
        for item in rubric_criteria
    )


def effective_pass_mode(step: dict, rubric_present: bool) -> str:
    """
    這一關實際用的通過方式。

    沒填、或填了不認得的值 → score（舊資料、還沒跑 019）。
    機器人沒有評分規準卻設成 score → complete：沒有分數可以比門檻，不能讓關卡永遠過不了。
    """
    mode = step.get("pass_mode") if isinstance(step, dict) else None
    if mode not in PASS_MODES:
        mode = DEFAULT_PASS_MODE
    if mode == "score" and not rubric_present:
        return "complete"
    return mode


def status_after_submit(mode: str, ai_passed: Optional[bool]) -> Optional[str]:
    """
    送出作答、AI 處理完之後 step_attempts.status 要改成什麼；None = 不改（維持 submitted）。

    Args:
        mode:      effective_pass_mode() 的結果
        ai_passed: AI 評分是否過門檻；沒評分（只給提示、轉介、沒有評分規準）為 None
    """
    if mode == "complete":
        return "passed"
    if mode == "teacher_review":
        return None  # 等老師複核
    if ai_passed is None:
        return "revision_required"
    return "passed" if ai_passed else "revision_required"


def status_after_review(
    mode: str,
    decision: str,
    final_score: Optional[int],
    pass_score: int,
) -> Optional[str]:
    """
    教師判定後 step_attempts.status 要改成什麼；None = 不改。

    - 要求重寫（request_retry）一律 revision_required（所有通過方式都一樣）
    - teacher_review：其他判定都代表老師看過了；有給分就比門檻，沒給分就算通過
    - score / complete：維持原本的做法，是否通過看已發布的教師分數（attempt_passed）
    """
    if decision == "request_retry":
        return "revision_required"
    if mode != "teacher_review":
        return None
    if final_score is None:
        return "passed"
    return "passed" if final_score >= pass_score else "revision_required"
