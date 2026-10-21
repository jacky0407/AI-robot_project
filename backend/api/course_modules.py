"""
指派 API：老師把學習模組指派給課程

資料存在 course_modules（migrations/005 建立）。指派後，該課程 active 的成員
就看得到模組裡的步驟與機器人；這部分由 005 的 RLS 規則判斷，這裡只負責寫入指派。

未發布的模組也可以先指派（老師可以先排課），學生要等模組發布後才看得到。
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError
from pydantic import BaseModel

from api.courses import _get_course_or_404
from core.auth import CurrentUser, get_current_user, require_staff
from database.client import get_supabase

router = APIRouter(prefix="/api/courses", tags=["Course Modules"])

# PostgreSQL：違反 UNIQUE
UNIQUE_VIOLATION = "23505"

ASSIGNMENT_SELECT = (
    "module_id, assigned_at, "
    "learning_modules(title, description, domain, is_published)"
)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _to_output(row: dict) -> dict:
    module = row.get("learning_modules") or {}
    return {
        "module_id": row["module_id"],
        "title": module.get("title"),
        "description": module.get("description"),
        "domain": module.get("domain"),
        "is_published": bool(module.get("is_published")),
        "assigned_at": row.get("assigned_at"),
    }


class AssignModuleRequest(BaseModel):
    module_id: UUID


@router.get("/{course_id}/modules")
async def list_course_modules(course_id: UUID, user: CurrentUser = Depends(get_current_user)):
    """
    課程已指派的模組。
    教師看全部；學生必須是這門課 active 的成員，且只看得到已發布的模組。
    """
    supabase = get_supabase()
    course_id = str(course_id)

    if not user.is_staff:
        member = (
            supabase.table("course_members")
            .select("status")
            .eq("course_id", course_id)
            .eq("user_id", user.id)
            .limit(1)
            .execute()
        )
        if not member.data or member.data[0]["status"] != "active":
            raise _error(status.HTTP_403_FORBIDDEN, "NOT_COURSE_MEMBER", "您尚未加入這門課程")
    else:
        _get_course_or_404(supabase, course_id)

    res = (
        supabase.table("course_modules")
        .select(ASSIGNMENT_SELECT)
        .eq("course_id", course_id)
        .order("assigned_at", desc=False)
        .execute()
    )
    modules = [_to_output(row) for row in res.data or []]
    if not user.is_staff:
        modules = [m for m in modules if m["is_published"]]
    return {"data": modules}


@router.post("/{course_id}/modules", status_code=status.HTTP_201_CREATED)
async def assign_module(course_id: UUID, body: AssignModuleRequest, user: CurrentUser = Depends(require_staff)):
    """把模組指派給課程"""
    supabase = get_supabase()
    course_id, module_id = str(course_id), str(body.module_id)

    _get_course_or_404(supabase, course_id)
    module = (
        supabase.table("learning_modules")
        .select("title, description, domain, is_published")
        .eq("id", module_id)
        .limit(1)
        .execute()
    )
    if not module.data:
        raise _error(status.HTTP_404_NOT_FOUND, "MODULE_NOT_FOUND", "找不到這個模組")

    try:
        res = (
            supabase.table("course_modules")
            .insert({"course_id": course_id, "module_id": module_id, "assigned_by": user.id})
            .execute()
        )
    except APIError as e:
        if e.code == UNIQUE_VIOLATION:
            raise _error(status.HTTP_409_CONFLICT, "ALREADY_ASSIGNED", "這個模組已經指派給這門課程")
        raise

    row = {**res.data[0], "learning_modules": module.data[0]}
    message = "已指派模組" if module.data[0].get("is_published") else "已指派模組（模組尚未發布，學生暫時看不到）"
    return {"data": _to_output(row), "message": message}


@router.delete("/{course_id}/modules/{module_id}")
async def unassign_module(course_id: UUID, module_id: UUID, user: CurrentUser = Depends(require_staff)):
    """取消指派；學生的作答紀錄不受影響"""
    supabase = get_supabase()
    res = (
        supabase.table("course_modules")
        .delete()
        .eq("course_id", str(course_id))
        .eq("module_id", str(module_id))
        .execute()
    )
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "ASSIGNMENT_NOT_FOUND", "這門課程沒有指派這個模組")
    return {"success": True, "message": "已取消指派"}
