"""
作答權限與每日額度：學生送出作答前，確認他被指派了這個步驟，自主模式再扣一次額度

指派規則與 supabase/migrations/005_rls_policies.sql 一致，兩種任一成立即可：
    course      課程指派：模組指派給課程，學生是該課程 active 成員，課程未封存
    permission  逐位核准：tool_permissions 核准使用這支機器人（tool_id 為 NULL 代表全部），
                且今天（台灣時間）在 start_date ~ expire_date 之間
兩種都要求模組已發布、機器人為 published。教師端（owner / assistant）不檢查。

課程指派另外看學生加入時用的邀請碼（migrations/013）：
    - 過了邀請碼的可用日期 access_until（台灣時間）→ 403 ACCESS_EXPIRED
    - 免費方案（plan = free）→ 每天 daily_quota 次，和自主模式一樣扣次數
    - 自備金鑰方案（plan = self_key）→ 第二階段（/api/me/api-key）上線前一律 403 API_KEY_REQUIRED
    - 沒有掛邀請碼（舊資料或後台直接加入）→ 不限期、不限次數
    課程被擋時，若同時有自主模式的有效核准，改用核准那筆。

額度：每次送出扣 1，只拿到提示的那次也算；AI 評分因系統錯誤失敗時退回（migrations/011、013）。
扣次數由資料庫函式一步完成（consume_tool_quota / consume_course_quota），避免連按時超用；
送出前查剩餘次數用 quota_status()，只查不扣。

後端用 service_role 連線，不受 RLS 限制，所以這些檢查必須在這裡做。
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import HTTPException, status

from core.auth import CurrentUser

logger = logging.getLogger(__name__)

# 台灣沒有日光節約時間，用固定 +8（與 api/tool_requests.py 相同）
TAIPEI = timezone(timedelta(hours=8))

AccessMode = Literal["staff", "course", "permission"]

PERMISSION_FIELDS = "id, tool_id, start_date, expire_date, daily_quota, used_today, used_date"

# course_members 同時連到 courses 與 course_invite_codes，embed 要指定外鍵（見 api/courses.py 檔頭）
MEMBERSHIP_FIELDS = (
    "id, course_id, used_today, used_date, courses!course_members_course_id_fkey(is_archived), "
    "course_invite_codes!course_members_invite_code_id_fkey(plan, daily_quota, access_until)"
)

# 與 schema.sql 的 tool_permissions.daily_quota 預設值、consume_tool_quota() 的 COALESCE 一致
DEFAULT_DAILY_QUOTA = 10


@dataclass(frozen=True)
class StepAccess:
    mode: AccessMode
    # mode == "permission" 時，要扣額度的那筆 tool_permissions
    permission_id: Optional[str] = None
    # 同一筆的完整資料，給剩餘額度查詢用（不參與比較）
    permission: Optional[dict] = field(default=None, compare=False)
    # mode == "course" 且是免費方案時，要扣額度的那筆 course_members（不限次數時為 None）
    member_id: Optional[str] = None
    # 同一筆成員資料（含 used_today、邀請碼的 daily_quota / access_until），給剩餘額度查詢用
    member: Optional[dict] = field(default=None, compare=False)


def today_taipei() -> date:
    return datetime.now(TAIPEI).date()


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _not_assigned() -> HTTPException:
    return _error(status.HTTP_403_FORBIDDEN, "NOT_ASSIGNED", "您尚未被指派這個練習，請先加入課程或申請使用這支機器人")


def _parse_date(value) -> Optional[date]:
    return date.fromisoformat(value) if value else None


@dataclass(frozen=True)
class CourseMembership:
    # 可以作答的成員資料（優先順序：不限次數 > 免費方案）；沒有就是 None
    usable: Optional[dict] = None
    # 有被指派、但因為可用日期已過而不能用
    expired: bool = False
    # 有被指派、但只有自備金鑰方案（第二階段前不能作答）
    needs_api_key: bool = False


def _membership_for_module(db, user_id: str, module_id: str, today: date) -> CourseMembership:
    """學生透過課程被指派這個模組的狀況；一個學生可能從多門課被指派同一個模組"""
    assignments = db.table("course_modules").select("course_id").eq("module_id", module_id).execute()
    course_ids = [row["course_id"] for row in assignments.data or []]
    if not course_ids:
        return CourseMembership()

    memberships = (
        db.table("course_members")
        .select(MEMBERSHIP_FIELDS)
        .eq("user_id", user_id)
        .eq("status", "active")
        .in_("course_id", course_ids)
        .execute()
    )
    unlimited, free, expired, self_key = [], [], False, False
    for row in memberships.data or []:
        if (row.get("courses") or {}).get("is_archived"):
            continue
        invite = row.get("course_invite_codes")
        if not invite:
            unlimited.append(row)
            continue
        access_until = _parse_date(invite.get("access_until"))
        if access_until is not None and access_until < today:
            expired = True
        elif invite.get("plan") == "self_key":
            self_key = True
        else:
            free.append(row)

    usable = (unlimited or free or [None])[0]
    return CourseMembership(usable=usable, expired=expired, needs_api_key=self_key)


def _find_permission(db, user_id: str, tool_id: str, today: date) -> tuple[Optional[dict], bool]:
    """
    回傳 (今天有效的核准, 是否有已過期的核准)。
    同時有「指定這支機器人」與「全部機器人」的核准時，優先用指定的那筆。
    """
    res = (
        db.table("tool_permissions")
        .select(PERMISSION_FIELDS)
        .eq("user_id", user_id)
        .eq("status", "approved")
        # tool_id 來自資料庫的 module_steps（UUID），不是使用者輸入
        .or_(f"tool_id.eq.{tool_id},tool_id.is.null")
        .execute()
    )
    valid, expired = [], False
    for row in res.data or []:
        start, expire = _parse_date(row.get("start_date")), _parse_date(row.get("expire_date"))
        if expire is not None and expire < today:
            expired = True
        elif start is None or start <= today:
            valid.append(row)

    valid.sort(key=lambda row: row.get("tool_id") is None)
    return (valid[0] if valid else None), expired


def check_step_access(db, user: CurrentUser, step: dict, today: Optional[date] = None) -> StepAccess:
    """
    step 需含 module_id、tool_id、ai_tools.status（submit 查的 module_steps 已帶出）。
    沒有權限時拋 HTTPException（403）。
    """
    if user.is_staff:
        return StepAccess(mode="staff")

    tool = step.get("ai_tools") or {}
    if tool.get("status") != "published":
        raise _not_assigned()

    module = (
        db.table("learning_modules")
        .select("is_published")
        .eq("id", step["module_id"])
        .limit(1)
        .execute()
    )
    if not module.data or not module.data[0].get("is_published"):
        raise _not_assigned()

    today = today or today_taipei()
    membership = _membership_for_module(db, user.id, step["module_id"], today)
    if membership.usable is not None:
        member = membership.usable
        if member.get("course_invite_codes"):
            return StepAccess(mode="course", member_id=member["id"], member=member)
        return StepAccess(mode="course")

    permission, expired = _find_permission(db, user.id, step["tool_id"], today)
    if permission:
        return StepAccess(mode="permission", permission_id=permission["id"], permission=permission)
    if membership.needs_api_key:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "API_KEY_REQUIRED",
            "這門課需要使用自己的 Gemini 金鑰作答，請先到個人資料頁填寫金鑰",
        )
    if membership.expired:
        raise _error(status.HTTP_403_FORBIDDEN, "ACCESS_EXPIRED", "這門課的可用日期已過，請向老師索取新的邀請碼")
    if expired:
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_EXPIRED", "這支機器人的使用期限已過，請重新申請")
    raise _not_assigned()


def quota_status(access: StepAccess, today: Optional[date] = None) -> dict:
    """
    剩餘額度（只查不扣）。跨日時 used 視為 0，規則與 consume_tool_quota() / consume_course_quota() 一致。
    不限次數（staff、沒掛邀請碼的課程成員）時 used / quota / remaining / expire_date 都是 None。
    課程免費方案的 expire_date 是邀請碼的可用日期。
    """
    today = today or today_taipei()
    if access.mode == "permission":
        row = access.permission or {}
        quota, expire_date = row.get("daily_quota"), row.get("expire_date")
    elif access.mode == "course" and access.member_id:
        row = access.member or {}
        invite = row.get("course_invite_codes") or {}
        quota, expire_date = invite.get("daily_quota"), invite.get("access_until")
    else:
        return {"mode": access.mode, "used": None, "quota": None, "remaining": None, "expire_date": None}

    quota = DEFAULT_DAILY_QUOTA if quota is None else quota
    used = (row.get("used_today") or 0) if _parse_date(row.get("used_date")) == today else 0
    return {
        "mode": access.mode,
        "used": used,
        "quota": quota,
        "remaining": max(quota - used, 0),
        "expire_date": expire_date,
    }


def _quota_rpc(access: StepAccess, action: Literal["consume", "refund"]) -> Optional[tuple[str, dict]]:
    """要呼叫的資料庫函式與參數；不限次數時回 None"""
    if access.mode == "permission":
        return f"{action}_tool_quota", {"p_permission_id": access.permission_id}
    if access.mode == "course" and access.member_id:
        return f"{action}_course_quota", {"p_member_id": access.member_id}
    return None


def consume_quota(db, access: StepAccess, today: Optional[date] = None) -> Optional[dict]:
    """
    自主模式、課程免費方案扣一次額度；不限次數時不扣，回傳 None。
    額度用完時拋 HTTPException（429），不扣。
    成功回傳 {"used": 今天已用次數, "quota": 每日上限}。
    """
    rpc = _quota_rpc(access, "consume")
    if rpc is None:
        return None

    name, params = rpc
    res = db.rpc(name, {**params, "p_today": (today or today_taipei()).isoformat()}).execute()
    row = (res.data or [None])[0]
    if row is None:
        # 核准紀錄或成員資料在檢查後被刪除：當作沒有權限
        raise _not_assigned()
    if not row["allowed"]:
        raise _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "QUOTA_EXCEEDED",
            f"今天的 {row['quota']} 次作答已用完，明天（台灣時間 0 點）會恢復",
        )
    return {"used": row["used"], "quota": row["quota"]}


def refund_quota(db, access: StepAccess, today: Optional[date] = None) -> Optional[dict]:
    """
    AI 評分因系統錯誤失敗時退回 1 次額度（KNOWN_GAPS #18，migrations/011、013）。

    只有有扣次數的模式會退；跨日、已是 0 次時資料庫不退，回傳 None。
    這是在 SSE 串流中途呼叫的補救動作，失敗只記 log、不往外拋，
    以免蓋掉原本要回給學生的錯誤訊息（例如還沒跑 migration 011）。
    成功回傳 {"used": 退回後今天已用次數, "quota": 每日上限}。
    """
    rpc = _quota_rpc(access, "refund")
    if rpc is None:
        return None
    name, params = rpc
    try:
        res = db.rpc(name, {**params, "p_today": (today or today_taipei()).isoformat()}).execute()
    except Exception as e:  # noqa: BLE001 - 補救動作失敗不影響主流程
        logger.warning(f"退回額度失敗（{name} {params}）：{e}")
        return None
    row = (res.data or [None])[0]
    if row is None:
        return None
    return {"used": row["used"], "quota": row["quota"]}
