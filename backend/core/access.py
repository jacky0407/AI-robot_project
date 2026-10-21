"""
作答權限與每日額度：學生送出作答前，確認他被指派了這個步驟，自主模式再扣一次額度

指派規則與 supabase/migrations/005_rls_policies.sql 一致，兩種任一成立即可：
    course      課程指派：模組指派給課程，學生是該課程 active 成員，課程未封存
    permission  逐位核准：tool_permissions 核准使用這支機器人（tool_id 為 NULL 代表全部），
                且今天（台灣時間）在 start_date ~ expire_date 之間
兩種都要求模組已發布、機器人為 published。教師端（owner / assistant）不檢查。

額度（10/18 做法 A）只管 permission 模式：每次送出扣 1，只拿到提示的那次也算；
AI 評分失敗不退回（見 docs/KNOWN_GAPS.md）。課程指派的學生不扣次數。
扣次數由資料庫函式 consume_tool_quota() 一步完成（migrations/008），避免連按時超用；
送出前查剩餘次數用 quota_status()，只查不扣。

後端用 service_role 連線，不受 RLS 限制，所以這些檢查必須在這裡做。
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import HTTPException, status

from core.auth import CurrentUser

# 台灣沒有日光節約時間，用固定 +8（與 api/tool_requests.py 相同）
TAIPEI = timezone(timedelta(hours=8))

AccessMode = Literal["staff", "course", "permission"]

PERMISSION_FIELDS = "id, tool_id, start_date, expire_date, daily_quota, used_today, used_date"

# 與 schema.sql 的 tool_permissions.daily_quota 預設值、consume_tool_quota() 的 COALESCE 一致
DEFAULT_DAILY_QUOTA = 10


@dataclass(frozen=True)
class StepAccess:
    mode: AccessMode
    # mode == "permission" 時，要扣額度的那筆 tool_permissions
    permission_id: Optional[str] = None
    # 同一筆的完整資料，給剩餘額度查詢用（不參與比較）
    permission: Optional[dict] = field(default=None, compare=False)


def today_taipei() -> date:
    return datetime.now(TAIPEI).date()


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _not_assigned() -> HTTPException:
    return _error(status.HTTP_403_FORBIDDEN, "NOT_ASSIGNED", "您尚未被指派這個練習，請先加入課程或申請使用這支機器人")


def _parse_date(value) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _assigned_via_course(db, user_id: str, module_id: str) -> bool:
    assignments = db.table("course_modules").select("course_id").eq("module_id", module_id).execute()
    course_ids = [row["course_id"] for row in assignments.data or []]
    if not course_ids:
        return False

    memberships = (
        db.table("course_members")
        .select("course_id, courses(is_archived)")
        .eq("user_id", user_id)
        .eq("status", "active")
        .in_("course_id", course_ids)
        .execute()
    )
    return any(not (row.get("courses") or {}).get("is_archived") for row in memberships.data or [])


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

    if _assigned_via_course(db, user.id, step["module_id"]):
        return StepAccess(mode="course")

    permission, expired = _find_permission(db, user.id, step["tool_id"], today or today_taipei())
    if permission:
        return StepAccess(mode="permission", permission_id=permission["id"], permission=permission)
    if expired:
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_EXPIRED", "這支機器人的使用期限已過，請重新申請")
    raise _not_assigned()


def quota_status(access: StepAccess, today: Optional[date] = None) -> dict:
    """
    剩餘額度（只查不扣）。跨日時 used 視為 0，規則與 consume_tool_quota() 一致。
    course / staff 模式不限次數，used / quota / remaining / expire_date 都是 None。
    """
    if access.mode != "permission":
        return {"mode": access.mode, "used": None, "quota": None, "remaining": None, "expire_date": None}

    row = access.permission or {}
    today = today or today_taipei()
    quota = row.get("daily_quota")
    quota = DEFAULT_DAILY_QUOTA if quota is None else quota
    used = (row.get("used_today") or 0) if _parse_date(row.get("used_date")) == today else 0
    return {
        "mode": "permission",
        "used": used,
        "quota": quota,
        "remaining": max(quota - used, 0),
        "expire_date": row.get("expire_date"),
    }


def consume_quota(db, access: StepAccess, today: Optional[date] = None) -> Optional[dict]:
    """
    permission 模式扣一次額度；其他模式不扣，回傳 None。
    額度用完時拋 HTTPException（429），不扣。
    成功回傳 {"used": 今天已用次數, "quota": 每日上限}。
    """
    if access.mode != "permission":
        return None

    res = db.rpc(
        "consume_tool_quota",
        {"p_permission_id": access.permission_id, "p_today": (today or today_taipei()).isoformat()},
    ).execute()
    row = (res.data or [None])[0]
    if row is None:
        # 核准紀錄在檢查後被刪除：當作沒有權限
        raise _not_assigned()
    if not row["allowed"]:
        raise _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "QUOTA_EXCEEDED",
            f"今天的 {row['quota']} 次作答已用完，明天（台灣時間 0 點）會恢復",
        )
    return {"used": row["used"], "quota": row["quota"]}
