"""
帳號停用、去識別化與永久刪除（#87，需求說明書 ACC-006、PRI-005，migration 027）

狀態（profiles.account_status）與能做的事：

    active ──停用──▶ disabled ──恢復──▶ active
       │                │
       ├──去識別化──────┴─▶ deidentified（姓名、Email 換成匿名研究編號；不能復原）
       │
       └──永久刪除（active / disabled / deidentified 都可以）──▶ pending_deletion
                 30 天內可以「復原」回到原本的狀態；到期後「清除到期帳號」才真的刪除

誰可以做：
  - 停用、恢復：教授與助理；助理只能對學生帳號操作
  - 去識別化：只限教授，只能對學生帳號
  - 永久刪除、復原、清除到期帳號：只限教授；永久刪除與清除要 5 分鐘內重新輸入過驗證碼（api/accounts.py）
  - 任何人都不能對自己操作；最後一位使用中的教授不能被停用或刪除

不能登入的做法：
  - profiles.is_active = false（後端 core/auth.py 一律擋 403 ACCOUNT_DISABLED，前端 middleware 也會登出）
  - Supabase Auth 封鎖（ban_duration）：連登入、更新 token 都會失敗

永久刪除會刪掉 Supabase Auth 的帳號 → profiles → 這個人自己的資料（作答、評分、選課、同意書、聊天…）一起刪除。
老師帳號被刪除時，他寫過的教師判定保留（migration 027 把 teacher_reviews.teacher_id 改成 SET NULL）。
每個操作都寫進操作紀錄（core/audit.py）。
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.audit import record
from core.auth import STAFF_ROLES, CurrentUser

logger = logging.getLogger(__name__)

STATUSES = ("active", "disabled", "deidentified", "pending_deletion")
RECOVERY_DAYS = 30
# Supabase Auth 封鎖登入的時間：100 年（等同永久，解除時設成 "none"）
BAN_FOREVER = "876000h"
DEIDENTIFIED_EMAIL_DOMAIN = "deidentified.invalid"

PROFILE_FIELDS = (
    "id, email, full_name, role, is_active, account_status, status_reason, status_changed_at, "
    "status_changed_by, deletion_scheduled_for, status_before_deletion, created_at"
)


class AccountError(Exception):
    """不能做這個操作（權限、狀態不對），API 轉成對應的 HTTP 狀態"""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def status_of(profile: dict) -> str:
    """還沒跑 027 的舊資料：用 is_active 推回狀態"""
    value = profile.get("account_status")
    if value in STATUSES:
        return value
    return "active" if profile.get("is_active") is not False else "disabled"


# ── 權限 ───────────────────────────────────────────────────────────────────


def _active_owner_count(db) -> int:
    rows = db.table("profiles").select("id, role, is_active, account_status").eq("role", "owner").execute().data or []
    return sum(1 for r in rows if status_of(r) == "active")


def check_can_manage(db, actor: CurrentUser, target: dict, action: str) -> None:
    """
    action：disable / enable / deidentify / delete / restore
    不能做就拋 AccountError（403 / 409）。
    """
    if target["id"] == actor.id:
        raise AccountError(403, "CANNOT_MANAGE_SELF", "不能對自己的帳號做這個操作")

    target_is_staff = target.get("role") in STAFF_ROLES
    if action in ("deidentify", "delete", "restore") and actor.role != "owner":
        raise AccountError(403, "PERMISSION_DENIED", "只有教授可以去識別化或永久刪除帳號")
    if actor.role != "owner" and target_is_staff:
        raise AccountError(403, "PERMISSION_DENIED", "助理只能停用或恢復學生帳號")
    if action == "deidentify" and target_is_staff:
        raise AccountError(400, "STAFF_CANNOT_BE_DEIDENTIFIED", "去識別化只適用學生帳號；教師帳號請改用停用或永久刪除")

    # 最後一位使用中的教授不能被停用或刪除，否則沒有人能管理平台
    if action in ("disable", "delete") and target.get("role") == "owner" and status_of(target) == "active":
        if _active_owner_count(db) <= 1:
            raise AccountError(409, "LAST_OWNER", "這是最後一位使用中的教授帳號，不能停用或刪除")


def allowed_actions(actor: CurrentUser, target: dict) -> list[str]:
    """這位操作者可以對這個帳號做哪些事（前端用來顯示按鈕；送出時後端會再檢查一次）"""
    if target["id"] == actor.id:
        return []
    status = status_of(target)
    target_is_staff = target.get("role") in STAFF_ROLES
    actions = []
    if actor.role == "owner" or not target_is_staff:
        if status == "active":
            actions.append("disable")
        if status == "disabled":
            actions.append("enable")
    if actor.role == "owner":
        if status in ("active", "disabled") and not target_is_staff:
            actions.append("deidentify")
        if status in ("active", "disabled", "deidentified"):
            actions.append("delete")
        if status == "pending_deletion":
            actions.append("restore")
    return actions


# ── 共用 ───────────────────────────────────────────────────────────────────


def get_profile(db, user_id: str) -> dict:
    res = db.table("profiles").select(PROFILE_FIELDS).eq("id", user_id).limit(1).execute()
    if not res.data:
        raise AccountError(404, "ACCOUNT_NOT_FOUND", "找不到這個帳號")
    return res.data[0]


def _require_status(profile: dict, allowed: tuple[str, ...], action_label: str) -> str:
    current = status_of(profile)
    if current not in allowed:
        raise AccountError(409, "INVALID_STATUS_TRANSITION", f"這個帳號目前是「{STATUS_LABELS[current]}」，不能{action_label}")
    return current


STATUS_LABELS = {
    "active": "使用中",
    "disabled": "已停用",
    "deidentified": "已去識別化",
    "pending_deletion": "待刪除",
}


def _set_login_ban(db, user_id: str, banned: bool) -> None:
    """Supabase Auth 封鎖或解除封鎖登入；失敗就拋錯（不能讓「已停用」的人還登得進去）"""
    try:
        db.auth.admin.update_user_by_id(user_id, {"ban_duration": BAN_FOREVER if banned else "none"})
    except Exception as e:  # noqa: BLE001
        logger.exception("Supabase Auth 封鎖設定失敗（user_id=%s）", user_id)
        raise AccountError(502, "AUTH_UPDATE_FAILED", "無法更新登入權限，請稍後再試") from e


def _update_profile(db, user_id: str, actor: CurrentUser, changes: dict) -> dict:
    changes = {**changes, "status_changed_at": _now().isoformat(), "status_changed_by": actor.id}
    if "account_status" in changes:
        changes["is_active"] = changes["account_status"] == "active"
    res = db.table("profiles").update(changes).eq("id", user_id).execute()
    return res.data[0] if res.data else {**changes, "id": user_id}


# ── 操作 ───────────────────────────────────────────────────────────────────


def disable(db, actor: CurrentUser, user_id: str, reason: str) -> dict:
    target = get_profile(db, user_id)
    check_can_manage(db, actor, target, "disable")
    _require_status(target, ("active",), "停用")
    _set_login_ban(db, user_id, True)
    updated = _update_profile(db, user_id, actor, {"account_status": "disabled", "status_reason": reason})
    record(db, actor, "account.disable", target_type="account", target_id=user_id,
           detail={"role": target.get("role"), "reason": reason})
    return updated


def enable(db, actor: CurrentUser, user_id: str, reason: Optional[str] = None) -> dict:
    target = get_profile(db, user_id)
    check_can_manage(db, actor, target, "enable")
    _require_status(target, ("disabled",), "恢復")
    _set_login_ban(db, user_id, False)
    updated = _update_profile(db, user_id, actor, {"account_status": "active", "status_reason": reason})
    record(db, actor, "account.enable", target_type="account", target_id=user_id,
           detail={"role": target.get("role"), "reason": reason})
    return updated


def research_id_for(db, user_id: str) -> str:
    """這個人的匿名研究編號（註冊時就產生在 research_consents）；沒有就產生一組"""
    res = db.table("research_consents").select("anonymous_research_id").eq("user_id", user_id).limit(1).execute()
    if res.data and res.data[0].get("anonymous_research_id"):
        return res.data[0]["anonymous_research_id"]
    return f"SPED-{uuid.uuid4().hex[:8]}"


def deidentify(db, actor: CurrentUser, user_id: str, reason: str) -> dict:
    """
    姓名、Email 換成匿名研究編號，學習歷程保留；不能復原。
    Supabase Auth 的 Email 也換掉並封鎖登入（原本的 Email 不再留在任何地方）。
    """
    target = get_profile(db, user_id)
    check_can_manage(db, actor, target, "deidentify")
    _require_status(target, ("active", "disabled"), "去識別化")

    anon_id = research_id_for(db, user_id)
    anon_email = f"{anon_id.lower()}@{DEIDENTIFIED_EMAIL_DOMAIN}"
    try:
        db.auth.admin.update_user_by_id(user_id, {
            "email": anon_email,
            "email_confirm": True,
            "user_metadata": {},
            "ban_duration": BAN_FOREVER,
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("去識別化時更新 Supabase Auth 失敗（user_id=%s）", user_id)
        raise AccountError(502, "AUTH_UPDATE_FAILED", "無法更新登入帳號，去識別化沒有執行，請稍後再試") from e

    updated = _update_profile(db, user_id, actor, {
        "account_status": "deidentified",
        "status_reason": reason,
        "full_name": anon_id,
        "email": anon_email,
        "profession": None,
    })
    # 自備的 Gemini 金鑰屬於個人，去識別化後不需要也不該保留
    try:
        db.table("user_api_keys").delete().eq("user_id", user_id).execute()
    except Exception:  # noqa: BLE001
        logger.warning("去識別化時刪除自備金鑰失敗（user_id=%s）", user_id)

    # 紀錄裡不寫原本的姓名與 Email（寫了就等於沒去識別化）
    record(db, actor, "account.deidentify", target_type="account", target_id=user_id,
           detail={"research_id": anon_id, "reason": reason})
    return updated


def request_deletion(db, actor: CurrentUser, user_id: str, reason: str) -> dict:
    """申請永久刪除：先進入 30 天回收期（不能登入），期間可以復原"""
    target = get_profile(db, user_id)
    check_can_manage(db, actor, target, "delete")
    previous = _require_status(target, ("active", "disabled", "deidentified"), "永久刪除")
    _set_login_ban(db, user_id, True)
    scheduled = _now() + timedelta(days=RECOVERY_DAYS)
    updated = _update_profile(db, user_id, actor, {
        "account_status": "pending_deletion",
        "status_reason": reason,
        "deletion_scheduled_for": scheduled.isoformat(),
        "status_before_deletion": previous,
    })
    record(db, actor, "account.delete_request", target_type="account", target_id=user_id, detail={
        "role": target.get("role"),
        "reason": reason,
        "previous_status": previous,
        "deletion_scheduled_for": scheduled.isoformat(),
    })
    return updated


def restore(db, actor: CurrentUser, user_id: str) -> dict:
    """30 天回收期內復原：回到申請刪除前的狀態（使用中才解除登入封鎖）"""
    target = get_profile(db, user_id)
    check_can_manage(db, actor, target, "restore")
    _require_status(target, ("pending_deletion",), "復原")
    previous = target.get("status_before_deletion")
    previous = previous if previous in ("active", "disabled", "deidentified") else "disabled"
    if previous == "active":
        _set_login_ban(db, user_id, False)
    updated = _update_profile(db, user_id, actor, {
        "account_status": previous,
        "deletion_scheduled_for": None,
        "status_before_deletion": None,
    })
    record(db, actor, "account.restore", target_type="account", target_id=user_id, detail={"restored_to": previous})
    return updated


def due_for_purge(db, now: Optional[datetime] = None) -> list[dict]:
    """回收期已過、可以真正刪除的帳號"""
    now = now or _now()
    rows = db.table("profiles").select(PROFILE_FIELDS).eq("account_status", "pending_deletion").execute().data or []
    due = []
    for r in rows:
        scheduled = r.get("deletion_scheduled_for")
        if scheduled and datetime.fromisoformat(str(scheduled).replace("Z", "+00:00")) <= now:
            due.append(r)
    return due


def purge_due(db, actor: CurrentUser, now: Optional[datetime] = None) -> dict:
    """
    真正刪除回收期已過的帳號：刪 Supabase Auth 帳號 → profiles 與這個人自己的資料一起刪除（ON DELETE CASCADE）。
    每一個都寫操作紀錄（成功或失敗；紀錄不設外鍵，帳號刪除後仍然留著）。一個失敗不影響其他的。
    """
    if actor.role != "owner":
        raise AccountError(403, "PERMISSION_DENIED", "只有教授可以清除到期帳號")
    purged, failed = [], []
    for target in due_for_purge(db, now):
        if target["id"] == actor.id:
            continue
        try:
            db.auth.admin.delete_user(target["id"])
            purged.append(target["id"])
            ok = True
        except Exception:  # noqa: BLE001
            logger.exception("刪除帳號失敗（user_id=%s）", target["id"])
            failed.append(target["id"])
            ok = False
        record(db, actor, "account.purge", target_type="account", target_id=target["id"], detail={
            "role": target.get("role"),
            "reason": target.get("status_reason"),
            "requested_at": target.get("status_changed_at"),
            "deleted": ok,
        })
    return {"purged": purged, "failed": failed}
