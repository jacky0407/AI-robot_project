"""
課程 API：建立課程與邀請碼、用邀請碼加入、老師審核加入申請

邀請碼目前採「做法 A」：一門課一組邀請碼，存在 courses.invite_code。
之後改成「做法 B」（一門課多組邀請碼，見 docs/api.md）時：
    - 只要改 find_course_by_invite_code()，改查新的邀請碼表
    - POST /api/courses/join 的網址、request、response 欄位不變，前端不用重做

成員狀態（course_members.status，見 migrations/007）：
    pending  → 等老師核准      active   → 已加入
    rejected → 老師拒絕申請    removed  → 老師移出課程
被拒絕或移出的學生可以再輸入邀請碼重新申請，一律回到 pending，由老師決定。

權限：教師端（owner / assistant）可管理所有課程；學生端只能加入與查看自己的課程。
"""

import re
import secrets
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from core.auth import CurrentUser, get_current_user, require_staff
from database.client import get_supabase

router = APIRouter(prefix="/api/courses", tags=["Courses"])

# PostgreSQL：違反 UNIQUE
UNIQUE_VIOLATION = "23505"

# 去掉容易看錯的 0/O、1/I/L
INVITE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 8
INVITE_CODE_MAX_RETRIES = 5
# 做法 B 的自訂代碼（如 IEP-2026-GRANT）也要能通過
INVITE_CODE_PATTERN = re.compile(r"^[A-Z0-9-]{4,32}$")

MEMBER_STATUSES = ("pending", "active", "rejected", "removed")

# 審核動作 → (允許的原狀態, 新狀態)
MEMBER_ACTIONS = {
    "approve": (("pending", "rejected", "removed"), "active"),
    "reject": (("pending",), "rejected"),
    "remove": (("active", "pending"), "removed"),
}

COURSE_FIELDS = "id, title, description, invite_code, require_approval, is_archived, created_by, created_at"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def generate_invite_code() -> str:
    return "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


def normalize_invite_code(raw: str) -> str:
    return raw.strip().upper()


def find_course_by_invite_code(supabase, code: str) -> Optional[dict]:
    """
    用邀請碼找課程。做法 A 直接查 courses.invite_code。
    改成做法 B 時只改這裡：改查邀請碼表，再帶出對應的課程。
    """
    res = (
        supabase.table("courses")
        .select("id, title, require_approval, is_archived")
        .eq("invite_code", code)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _get_course_or_404(supabase, course_id: str) -> dict:
    res = supabase.table("courses").select(COURSE_FIELDS).eq("id", course_id).limit(1).execute()
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")
    return res.data[0]


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
    course = find_course_by_invite_code(supabase, code)
    if not course:
        raise _error(status.HTTP_404_NOT_FOUND, "INVALID_INVITE_CODE", "查無此課程代碼，請確認代碼是否輸入正確")
    if course.get("is_archived"):
        raise _error(status.HTTP_403_FORBIDDEN, "COURSE_ARCHIVED", "此課程已封存，無法加入")

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
        # rejected / removed 重新申請：一律交給老師決定，避免被移出的人用邀請碼直接回來
        res = (
            supabase.table("course_members")
            .update({"status": "pending"})
            .eq("id", existing.data[0]["id"])
            .execute()
        )
    else:
        new_status = "pending" if course.get("require_approval") else "active"
        try:
            res = (
                supabase.table("course_members")
                .insert({"course_id": course["id"], "user_id": user.id, "status": new_status})
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
        },
        "message": "已送出加入申請，請等待老師核准" if pending else "已成功加入課程",
    }


@router.get("/mine")
async def list_my_courses(user: CurrentUser = Depends(get_current_user)):
    """目前登入者加入（或申請中）的課程與狀態"""
    supabase = get_supabase()
    res = (
        supabase.table("course_members")
        .select("status, joined_at, courses(id, title, description, is_archived)")
        .eq("user_id", user.id)
        .order("joined_at", desc=True)
        .execute()
    )
    courses = []
    for row in res.data or []:
        course = row.get("courses") or {}
        courses.append({
            "course_id": course.get("id"),
            "course_name": course.get("title"),
            "description": course.get("description"),
            "is_archived": bool(course.get("is_archived")),
            "status": row["status"],
            "joined_at": row.get("joined_at"),
        })
    return {"data": courses}


# ──────────────────────────────────────────
# 教師端
# ──────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course(body: CreateCourseRequest, user: CurrentUser = Depends(require_staff)):
    """建立課程並自動產生邀請碼"""
    supabase = get_supabase()
    payload = {
        "title": body.title,
        "description": body.description or None,
        "require_approval": body.require_approval,
        "created_by": user.id,
    }

    # 邀請碼撞號機率極低，撞到就換一組重試
    for _ in range(INVITE_CODE_MAX_RETRIES):
        try:
            res = supabase.table("courses").insert({**payload, "invite_code": generate_invite_code()}).execute()
            return {"data": res.data[0], "message": "課程建立成功"}
        except APIError as e:
            if e.code != UNIQUE_VIOLATION:
                raise

    raise _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "INVITE_CODE_GENERATION_FAILED", "邀請碼產生失敗，請再試一次")


@router.get("")
async def list_courses(
    include_archived: bool = Query(False, description="True 時也列出已封存的課程"),
    user: CurrentUser = Depends(require_staff),
):
    """所有課程，附上已加入與待審核人數"""
    supabase = get_supabase()
    query = supabase.table("courses").select(f"{COURSE_FIELDS}, course_members(status)")
    if not include_archived:
        query = query.eq("is_archived", False)
    res = query.order("created_at", desc=True).execute()

    courses = []
    for row in res.data or []:
        members = row.pop("course_members", None) or []
        row["active_count"] = sum(1 for m in members if m.get("status") == "active")
        row["pending_count"] = sum(1 for m in members if m.get("status") == "pending")
        courses.append(row)
    return {"data": courses}


@router.patch("/{course_id}")
async def update_course(course_id: str, body: UpdateCourseRequest, user: CurrentUser = Depends(require_staff)):
    """修改課程名稱、說明、是否需審核、是否封存"""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise _error(status.HTTP_400_BAD_REQUEST, "NO_CHANGES", "沒有要修改的欄位")
    if changes.get("title") is None and "title" in changes:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_FORMAT", "課程名稱不可空白")

    supabase = get_supabase()
    res = supabase.table("courses").update(changes).eq("id", course_id).execute()
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")
    return {"data": res.data[0], "message": "課程已更新"}


@router.get("/{course_id}/members")
async def list_members(
    course_id: str,
    member_status: Optional[str] = Query(None, alias="status", description="pending | active | rejected | removed"),
    user: CurrentUser = Depends(require_staff),
):
    """課程成員；?status=pending 即為待審核名單"""
    if member_status is not None and member_status not in MEMBER_STATUSES:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_STATUS", f"status 只能是 {' / '.join(MEMBER_STATUSES)}")

    supabase = get_supabase()
    _get_course_or_404(supabase, course_id)

    query = (
        supabase.table("course_members")
        .select("user_id, status, joined_at, profiles(full_name, email)")
        .eq("course_id", course_id)
    )
    if member_status:
        query = query.eq("status", member_status)
    res = query.order("joined_at", desc=False).execute()

    members = []
    for row in res.data or []:
        profile = row.get("profiles") or {}
        members.append({
            "user_id": row["user_id"],
            "full_name": profile.get("full_name"),
            "email": profile.get("email"),
            "status": row["status"],
            "joined_at": row.get("joined_at"),
        })
    return {"data": members}


@router.patch("/{course_id}/members/{user_id}")
async def update_member(
    course_id: str,
    user_id: str,
    body: MemberActionRequest,
    user: CurrentUser = Depends(require_staff),
):
    """核准（approve）、拒絕（reject）或移出（remove）成員"""
    supabase = get_supabase()
    existing = (
        supabase.table("course_members")
        .select("id, status")
        .eq("course_id", course_id)
        .eq("user_id", user_id)
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
