"""
課程 API：建立課程、產生與管理邀請碼、用邀請碼加入、老師審核加入申請

邀請碼（migrations/013，「邀請碼與自備金鑰設計提案」第一階段）：
    - 建立課程時不產生邀請碼，老師另外產生，一門課可以有多組
    - 每組設定可用日期 access_until（用這組碼加入的學生可以用到這天，台灣時間當天仍可用）
      與方案 plan：free（平台金鑰，每天 daily_quota 次）或 self_key（學生自備金鑰，不限次數）
    - 邀請碼不會自動失效；老師可以手動停用（is_active），停用後不能再用它加入
    - 老師改了碼的可用日期或次數，已用這組碼加入的學生跟著變（成員只記錄 invite_code_id）
    額度與可用日期在送出作答時由 core/access.py 檢查。

成員狀態（course_members.status，見 migrations/007）：
    pending  → 等老師核准      active   → 已加入
    rejected → 老師拒絕申請    removed  → 老師移出課程
被拒絕或移出的學生可以再輸入邀請碼重新申請，一律回到 pending，由老師決定。

權限：教師端（owner / assistant）可管理所有課程；學生端只能加入與查看自己的課程。

⚠️ course_members 同時連到 courses 與 course_invite_codes，Supabase 的自動 join 會分不清走哪條關聯，
   下面的 select 都用 `資料表!外鍵名稱(...)` 明確指定。
"""

import re
import secrets
from datetime import date
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from core.access import today_taipei
from core.auth import CurrentUser, get_current_user, require_staff
from database.client import get_supabase

router = APIRouter(prefix="/api/courses", tags=["Courses"])

# PostgreSQL：違反 UNIQUE
UNIQUE_VIOLATION = "23505"

# 去掉容易看錯的 0/O、1/I/L
INVITE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 8
INVITE_CODE_MAX_RETRIES = 5
# 學生輸入時的格式檢查；保留 -，舊資料或之後的自訂代碼（如 IEP-2026-GRANT）也能通過
INVITE_CODE_PATTERN = re.compile(r"^[A-Z0-9-]{4,32}$")

# 免費方案沒填每日次數時的預設值（與 tool_permissions.daily_quota 預設一致）
DEFAULT_DAILY_QUOTA = 10

MEMBER_STATUSES = ("pending", "active", "rejected", "removed")

# 審核動作 → (允許的原狀態, 新狀態)
MEMBER_ACTIONS = {
    "approve": (("pending", "rejected", "removed"), "active"),
    "reject": (("pending",), "rejected"),
    "remove": (("active", "pending"), "removed"),
}

COURSE_FIELDS = "id, title, description, require_approval, is_archived, created_by, created_at"
CODE_FIELDS = "id, course_id, code, plan, daily_quota, access_until, is_active, created_at"

# 明確指定外鍵的 embed（見檔頭說明）
EMBED_COURSE_FROM_CODE = "courses!course_invite_codes_course_id_fkey"
EMBED_CODES_FROM_COURSE = "course_invite_codes!course_invite_codes_course_id_fkey"
EMBED_MEMBERS_FROM_COURSE = "course_members!course_members_course_id_fkey"
EMBED_CODE_FROM_MEMBER = "course_invite_codes!course_members_invite_code_id_fkey"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def generate_invite_code() -> str:
    return "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


def normalize_invite_code(raw: str) -> str:
    return raw.strip().upper()


def _access_expired(access_until: Optional[str], today: date) -> bool:
    return bool(access_until) and date.fromisoformat(access_until) < today


def find_invite_code(supabase, code: str) -> Optional[dict]:
    """用邀請碼找到那組碼與它的課程"""
    res = (
        supabase.table("course_invite_codes")
        .select(f"id, plan, daily_quota, access_until, is_active, "
                f"{EMBED_COURSE_FROM_CODE}(id, title, require_approval, is_archived)")
        .eq("code", code)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _get_course_or_404(supabase, course_id: str) -> dict:
    res = supabase.table("courses").select(COURSE_FIELDS).eq("id", course_id).limit(1).execute()
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")
    return res.data[0]


def _code_output(row: dict, members: list[dict], today: date) -> dict:
    """邀請碼給前端的樣子，附上用這組碼加入（active）的人數"""
    return {
        "id": row["id"],
        "code": row["code"],
        "plan": row["plan"],
        "daily_quota": row.get("daily_quota"),
        "access_until": row["access_until"],
        "is_active": row["is_active"],
        "expired": _access_expired(row["access_until"], today),
        "created_at": row.get("created_at"),
        "member_count": sum(
            1 for m in members if m.get("invite_code_id") == row["id"] and m.get("status") == "active"
        ),
    }


def _validate_access_until(value: date, today: date) -> None:
    if value < today:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_ACCESS_UNTIL", "可用日期不能早於今天")


# ──────────────────────────────────────────
# Request models
# ──────────────────────────────────────────

class CreateCourseRequest(BaseModel):
    model_config = {"str_strip_whitespace": True}

    title: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    require_approval: bool = False


class UpdateCourseRequest(BaseModel):
    model_config = {"str_strip_whitespace": True}

    title: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    require_approval: Optional[bool] = None
    is_archived: Optional[bool] = None


class CreateInviteCodeRequest(BaseModel):
    plan: Literal["free", "self_key"] = "free"
    access_until: date
    # 只有免費方案用；沒填預設 10。自備金鑰不限次數，填了也不存
    daily_quota: Optional[int] = Field(None, ge=1, le=1000)


class UpdateInviteCodeRequest(BaseModel):
    # 方案不能改：改了會讓已加入的學生從「不限次數」突然變成有上限（或反過來），要改請產生新的碼
    is_active: Optional[bool] = None
    access_until: Optional[date] = None
    daily_quota: Optional[int] = Field(None, ge=1, le=1000)


class JoinCourseRequest(BaseModel):
    invite_code: str


class MemberActionRequest(BaseModel):
    action: Literal["approve", "reject", "remove"]


# ──────────────────────────────────────────
# 學生端
# ──────────────────────────────────────────

@router.post("/join")
async def join_course(body: JoinCourseRequest, user: CurrentUser = Depends(get_current_user)):
    """用邀請碼加入課程；課程需審核時狀態為 pending"""
    code = normalize_invite_code(body.invite_code)
    if not INVITE_CODE_PATTERN.match(code):
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_FORMAT", "課程邀請碼格式不正確，請確認後重新輸入")

    supabase = get_supabase()
    invite = find_invite_code(supabase, code)
    if not invite or not invite.get("courses"):
        raise _error(status.HTTP_404_NOT_FOUND, "INVALID_INVITE_CODE", "查無此課程代碼，請確認代碼是否輸入正確")
    course = invite["courses"]
    if not invite["is_active"]:
        raise _error(status.HTTP_403_FORBIDDEN, "CODE_DISABLED", "這組邀請碼已停用，請向老師索取新的邀請碼")
    if course.get("is_archived"):
        raise _error(status.HTTP_403_FORBIDDEN, "COURSE_ARCHIVED", "此課程已封存，無法加入")
    if _access_expired(invite["access_until"], today_taipei()):
        # 碼本身沒有失效，但用它加入也無法作答，直接說清楚
        raise _error(status.HTTP_403_FORBIDDEN, "CODE_EXPIRED", "這組邀請碼的可用日期已過，請向老師索取新的邀請碼")

    existing = (
        supabase.table("course_members")
        .select("id, status")
        .eq("course_id", course["id"])
        .eq("user_id", user.id)
        .limit(1)
        .execute()
    )

    if existing.data:
        current = existing.data[0]["status"]
        if current == "active":
            raise _error(status.HTTP_409_CONFLICT, "ALREADY_JOINED", "您已加入過此課程")
        if current == "pending":
            raise _error(status.HTTP_409_CONFLICT, "ALREADY_PENDING", "已送出加入申請，請等待老師核准")
        # rejected / removed 重新申請：一律交給老師決定，避免被移出的人用邀請碼直接回來；
        # 改掛在這次輸入的碼下，方案與可用日期以新的碼為準
        res = (
            supabase.table("course_members")
            .update({"status": "pending", "invite_code_id": invite["id"]})
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        new_status = "pending" if course.get("require_approval") else "active"
        try:
            res = (
                supabase.table("course_members")
                .insert({
                    "course_id": course["id"],
                    "user_id": user.id,
                    "status": new_status,
                    "invite_code_id": invite["id"],
                })
                .execute()
            )
        except APIError as e:
            # 同一人同時按兩次：第二次撞到 UNIQUE(course_id, user_id)
            if e.code == UNIQUE_VIOLATION:
                raise _error(status.HTTP_409_CONFLICT, "ALREADY_JOINED", "您已加入過此課程")
            raise

    member = res.data[0]
    pending = member["status"] == "pending"
    return {
        "data": {
            "course_id": course["id"],
            "course_name": course["title"],
            "status": member["status"],
            "joined_at": member.get("joined_at"),
            "plan": invite["plan"],
            "daily_quota": invite.get("daily_quota"),
            "access_until": invite["access_until"],
            # 第二階段（/api/me/api-key）上線前，自備金鑰方案的學生一律需要先填金鑰
            "needs_api_key": invite["plan"] == "self_key",
        },
        "message": "已送出加入申請，請等待老師核准" if pending else "已成功加入課程",
    }


@router.get("/mine")
async def list_my_courses(user: CurrentUser = Depends(get_current_user)):
    """目前登入者加入（或申請中）的課程、狀態、方案與可用日期"""
    supabase = get_supabase()
    res = (
        supabase.table("course_members")
        .select(f"status, joined_at, courses!course_members_course_id_fkey(id, title, description, is_archived), "
                f"{EMBED_CODE_FROM_MEMBER}(plan, daily_quota, access_until)")
        .eq("user_id", user.id)
        .order("joined_at", desc=True)
        .execute()
    )
    today = today_taipei()
    courses = []
    for row in res.data or []:
        course = row.get("courses") or {}
        invite = row.get("course_invite_codes") or {}
        plan = invite.get("plan")
        courses.append({
            "course_id": course.get("id"),
            "course_name": course.get("title"),
            "description": course.get("description"),
            "is_archived": bool(course.get("is_archived")),
            "status": row["status"],
            "joined_at": row.get("joined_at"),
            # 沒有掛邀請碼（舊資料或後台加入）：不限期、不限次數，三個欄位都是 null
            "plan": plan,
            "daily_quota": invite.get("daily_quota"),
            "access_until": invite.get("access_until"),
            "access_expired": _access_expired(invite.get("access_until"), today),
            "needs_api_key": plan == "self_key",
        })
    return {"data": courses}


# ──────────────────────────────────────────
# 教師端：課程
# ──────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course(body: CreateCourseRequest, user: CurrentUser = Depends(require_staff)):
    """建立課程（不產生邀請碼，請再用 POST /api/courses/{id}/invite-codes 產生）"""
    supabase = get_supabase()
    res = (
        supabase.table("courses")
        .insert({
            "title": body.title,
            "description": body.description or None,
            "require_approval": body.require_approval,
            "created_by": user.id,
        })
        .execute()
    )
    course = {key: res.data[0].get(key) for key in COURSE_FIELDS.split(", ")}
    return {"data": course, "message": "課程建立成功，請產生邀請碼讓學生加入"}


@router.get("")
async def list_courses(
    include_archived: bool = Query(False, description="True 時也列出已封存的課程"),
    user: CurrentUser = Depends(require_staff),
):
    """所有課程，附上已加入與待審核人數、所有邀請碼（新的在前）"""
    supabase = get_supabase()
    query = supabase.table("courses").select(
        f"{COURSE_FIELDS}, "
        f"{EMBED_MEMBERS_FROM_COURSE}(status, invite_code_id), "
        f"{EMBED_CODES_FROM_COURSE}({CODE_FIELDS})"
    )
    if not include_archived:
        query = query.eq("is_archived", False)
    res = query.order("created_at", desc=True).execute()

    today = today_taipei()
    courses = []
    for row in res.data or []:
        members = row.pop("course_members", None) or []
        codes = row.pop("course_invite_codes", None) or []
        codes.sort(key=lambda c: c.get("created_at") or "", reverse=True)
        row["active_count"] = sum(1 for m in members if m.get("status") == "active")
        row["pending_count"] = sum(1 for m in members if m.get("status") == "pending")
        row["invite_codes"] = [_code_output(c, members, today) for c in codes]
        courses.append(row)
    return {"data": courses}


@router.patch("/{course_id}")
async def update_course(course_id: UUID, body: UpdateCourseRequest, user: CurrentUser = Depends(require_staff)):
    """修改課程名稱、說明、是否需審核、是否封存"""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise _error(status.HTTP_400_BAD_REQUEST, "NO_CHANGES", "沒有要修改的欄位")
    if changes.get("title") is None and "title" in changes:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_FORMAT", "課程名稱不可空白")

    supabase = get_supabase()
    res = supabase.table("courses").update(changes).eq("id", str(course_id)).execute()
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")
    return {"data": res.data[0], "message": "課程已更新"}


# ──────────────────────────────────────────
# 教師端：邀請碼
# ──────────────────────────────────────────

def _course_members_for_counts(supabase, course_id: str) -> list[dict]:
    res = (
        supabase.table("course_members")
        .select("status, invite_code_id")
        .eq("course_id", course_id)
        .execute()
    )
    return res.data or []


@router.post("/{course_id}/invite-codes", status_code=status.HTTP_201_CREATED)
async def create_invite_code(
    course_id: UUID,
    body: CreateInviteCodeRequest,
    user: CurrentUser = Depends(require_staff),
):
    """產生一組邀請碼"""
    today = today_taipei()
    _validate_access_until(body.access_until, today)

    supabase = get_supabase()
    course_id = str(course_id)
    course = _get_course_or_404(supabase, course_id)
    if course.get("is_archived"):
        raise _error(status.HTTP_403_FORBIDDEN, "COURSE_ARCHIVED", "此課程已封存，請先取消封存再產生邀請碼")

    payload = {
        "course_id": course_id,
        "plan": body.plan,
        "daily_quota": (body.daily_quota or DEFAULT_DAILY_QUOTA) if body.plan == "free" else None,
        "access_until": body.access_until.isoformat(),
        "created_by": user.id,
    }
    # 邀請碼撞號機率極低，撞到就換一組重試
    for _ in range(INVITE_CODE_MAX_RETRIES):
        try:
            res = (
                supabase.table("course_invite_codes")
                .insert({**payload, "code": generate_invite_code()})
                .execute()
            )
            return {"data": _code_output(res.data[0], [], today), "message": "已產生邀請碼"}
        except APIError as e:
            if e.code != UNIQUE_VIOLATION:
                raise

    raise _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "INVITE_CODE_GENERATION_FAILED", "邀請碼產生失敗，請再試一次")


@router.get("/{course_id}/invite-codes")
async def list_invite_codes(course_id: UUID, user: CurrentUser = Depends(require_staff)):
    """這門課的所有邀請碼（新的在前），附上已加入人數"""
    supabase = get_supabase()
    course_id = str(course_id)
    _get_course_or_404(supabase, course_id)

    res = (
        supabase.table("course_invite_codes")
        .select(CODE_FIELDS)
        .eq("course_id", course_id)
        .order("created_at", desc=True)
        .execute()
    )
    members = _course_members_for_counts(supabase, course_id)
    today = today_taipei()
    return {"data": [_code_output(row, members, today) for row in res.data or []]}


@router.patch("/{course_id}/invite-codes/{code_id}")
async def update_invite_code(
    course_id: UUID,
    code_id: UUID,
    body: UpdateInviteCodeRequest,
    user: CurrentUser = Depends(require_staff),
):
    """停用／啟用、改可用日期或每日次數；已用這組碼加入的學生跟著變"""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise _error(status.HTTP_400_BAD_REQUEST, "NO_CHANGES", "沒有要修改的欄位")

    supabase = get_supabase()
    course_id = str(course_id)
    existing = (
        supabase.table("course_invite_codes")
        .select(CODE_FIELDS)
        .eq("id", str(code_id))
        .eq("course_id", course_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise _error(status.HTTP_404_NOT_FOUND, "INVITE_CODE_NOT_FOUND", "這門課程沒有這組邀請碼")
    plan = existing.data[0]["plan"]

    today = today_taipei()
    for field in ("is_active", "access_until"):
        if field in changes and changes[field] is None:
            raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_FORMAT", f"{field} 不可為空")
    if "access_until" in changes:
        _validate_access_until(changes["access_until"], today)
        changes["access_until"] = changes["access_until"].isoformat()
    if "daily_quota" in changes:
        if plan == "self_key":
            raise _error(status.HTTP_400_BAD_REQUEST, "QUOTA_NOT_APPLICABLE", "自備金鑰方案不限次數，不能設定每日次數")
        if changes["daily_quota"] is None:
            raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_FORMAT", "免費方案一定要有每日次數")

    res = (
        supabase.table("course_invite_codes")
        .update(changes)
        .eq("id", str(code_id))
        .execute()
    )
    members = _course_members_for_counts(supabase, course_id)
    return {"data": _code_output(res.data[0], members, today), "message": "邀請碼已更新"}


# ──────────────────────────────────────────
# 教師端：成員與審核
# ──────────────────────────────────────────

@router.get("/{course_id}/members")
async def list_members(
    course_id: UUID,
    member_status: Optional[str] = Query(None, alias="status", description="pending | active | rejected | removed"),
    user: CurrentUser = Depends(require_staff),
):
    """課程成員；?status=pending 即為待審核名單。附上加入時用的邀請碼與方案"""
    if member_status is not None and member_status not in MEMBER_STATUSES:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_STATUS", f"status 只能是 {' / '.join(MEMBER_STATUSES)}")

    supabase = get_supabase()
    course_id = str(course_id)
    _get_course_or_404(supabase, course_id)

    query = (
        supabase.table("course_members")
        .select(f"user_id, status, joined_at, profiles(full_name, email), "
                f"{EMBED_CODE_FROM_MEMBER}(code, plan, access_until)")
        .eq("course_id", course_id)
    )
    if member_status:
        query = query.eq("status", member_status)
    res = query.order("joined_at", desc=False).execute()

    members = []
    for row in res.data or []:
        profile = row.get("profiles") or {}
        invite = row.get("course_invite_codes") or {}
        members.append({
            "user_id": row["user_id"],
            "full_name": profile.get("full_name"),
            "email": profile.get("email"),
            "status": row["status"],
            "joined_at": row.get("joined_at"),
            "invite_code": invite.get("code"),
            "plan": invite.get("plan"),
            "access_until": invite.get("access_until"),
        })
    return {"data": members}


@router.patch("/{course_id}/members/{user_id}")
async def update_member(
    course_id: UUID,
    user_id: UUID,
    body: MemberActionRequest,
    user: CurrentUser = Depends(require_staff),
):
    """核准（approve）、拒絕（reject）或移出（remove）成員"""
    supabase = get_supabase()
    existing = (
        supabase.table("course_members")
        .select("id, status")
        .eq("course_id", str(course_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    if not existing.data:
        raise _error(status.HTTP_404_NOT_FOUND, "MEMBER_NOT_FOUND", "這位使用者沒有申請或加入這門課程")

    allowed_from, new_status = MEMBER_ACTIONS[body.action]
    current = existing.data[0]["status"]
    if current not in allowed_from:
        raise _error(
            status.HTTP_409_CONFLICT,
            "INVALID_STATUS_TRANSITION",
            f"目前狀態是 {current}，不能執行 {body.action}",
        )

    res = (
        supabase.table("course_members")
        .update({"status": new_status})
        .eq("id", existing.data[0]["id"])
        .execute()
    )
    return {"data": res.data[0], "message": "成員狀態已更新"}
