"""
操作稽核紀錄（#90，migration 026）：教師端「誰、在什麼時間、做了什麼」

用法：
    from core.audit import record
    record(db, user, "tool.update", target_type="tool", target_id=tool_id, detail={"version": 3})

規則：
  - audit_logs 只能新增，不能修改或刪除（資料庫觸發器擋住）
  - detail 只放 id、欄位名稱、版本號、數字；不放學生作答原文、姓名、Email 等個資
  - 寫入失敗只記 log、不擋住操作本身（跟 export_logs 一樣）；失敗時 log 裡有完整內容，可以事後補

action 命名：「對象.動作」，清單見 ACTIONS（查詢頁的篩選選單也用這份）。
"""

import logging
from typing import Any, Optional

from core.auth import CurrentUser

logger = logging.getLogger(__name__)

# 會記錄的操作與中文說明（前端查詢頁顯示用）
ACTIONS: dict[str, str] = {
    "tool.create": "建立機器人",
    "tool.update": "修改機器人",
    "tool.copy": "複製機器人",
    "tool.delete": "刪除機器人",
    "module.create": "建立模組",
    "module.update": "修改模組（含發布、取消發布）",
    "module.copy": "複製模組",
    "module.delete": "刪除模組",
    "step.create": "新增關卡",
    "step.update": "修改關卡（含停用、換機器人）",
    "step.delete": "刪除關卡",
    "review.judge": "教師判定",
    "export.research": "匯出研究資料",
    "export.teaching": "匯出教學資料",
    "permission.approve": "核准自主探索申請",
    "permission.reject": "拒絕或撤銷自主探索申請",
    "member.update": "修改課程成員狀態",
    "invite_code.delete": "刪除邀請碼（含移除學生）",
    "account.disable": "停用帳號",
    "account.enable": "恢復帳號",
    "account.deidentify": "帳號去識別化",
    "account.delete_request": "申請永久刪除帳號（30 天回收期）",
    "account.restore": "復原待刪除的帳號",
    "account.purge": "永久刪除帳號（回收期已過）",
}

MAX_DETAIL_TEXT = 200


def _clean(value: Any) -> Any:
    """detail 只留簡短的值：長字串截斷（避免不小心把作答原文、system_prompt 整段寫進來）"""
    if isinstance(value, str):
        return value if len(value) <= MAX_DETAIL_TEXT else value[:MAX_DETAIL_TEXT] + "…"
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value][:50]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def record(
    db,
    user: CurrentUser,
    action: str,
    *,
    target_type: Optional[str] = None,
    target_id: Optional[Any] = None,
    detail: Optional[dict] = None,
) -> None:
    """寫一筆操作紀錄；失敗只記 log，不擋住操作"""
    row = {
        "actor_id": user.id,
        "actor_role": user.role,
        "action": action,
        "target_type": target_type,
        "target_id": str(target_id) if target_id is not None else None,
        "detail": _clean(detail or {}),
    }
    try:
        db.table("audit_logs").insert(row).execute()
    except Exception:  # noqa: BLE001 - 稽核寫入失敗不擋操作，但一定要留下完整內容
        logger.exception("操作紀錄寫入失敗（請確認已執行 migration 026）：%s", row)
