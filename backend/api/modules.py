"""
模組與步驟管理 API：教授在後台建立學習模組、把機器人排成步驟 1、2、3…

權限（docs/api.md：建立與修改限 HOST）：
  - 讀取：教師端（owner / assistant）
  - 新增、修改、刪除、排序、發布：只限 owner（教授）

保護研究資料：
  - step_attempts.step_id 是 ON DELETE CASCADE，刪步驟會連學生作答一起刪掉，
    所以已有作答的步驟不能刪，只能停用（is_active = false，migration 010）；
    已有作答的模組也不能刪
  - 已有作答的步驟不能換機器人（不同機器人的作答混在同一步，研究資料就分不開了）

發布檢查：模組至少要有一個啟用中的步驟，且這些步驟的機器人都已發布，
否則學生進來會看到空模組或被 RLS 擋住的機器人。

學生端的解鎖規則（必修步驟通過才解鎖下一步）在 core/step_unlock.py。
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError
from pydantic import BaseModel, ConfigDict, Field

from core.auth import CurrentUser, require_staff
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/modules", tags=["Module Management"])

# PostgreSQL / PostgREST 錯誤代碼
UNIQUE_VIOLATION = "23505"
UNDEFINED_COLUMN = "42703"       # 還沒跑 migration 010（沒有 is_active）
FUNCTION_NOT_FOUND = "PGRST202"  # 還沒跑 migration 010（沒有 reorder_module_steps）

MODULE_FIELDS = "id, title, description, domain, is_published, created_at"
STEP_FIELDS = (
    "id, module_id, tool_id, step_order, step_title, is_required, "
    "pass_score, require_teacher_review, is_active"
)

DEFAULT_DOMAIN = "學前IEP"


# ──────────────────────────────────────────
# Request Models
# ──────────────────────────────────────────

class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class CreateModuleRequest(_Body):
    title: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    domain: str = Field(default=DEFAULT_DOMAIN, min_length=1, max_length=50)


class UpdateModuleRequest(_Body):
    title: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    domain: Optional[str] = Field(default=None, min_length=1, max_length=50)
    is_published: Optional[bool] = None


class CreateStepRequest(_Body):
    tool_id: UUID
    step_title: str = Field(min_length=1, max_length=100)
    is_required: bool = True
    pass_score: int = Field(default=70, ge=0, le=100)
    require_teacher_review: bool = False


class UpdateStepRequest(_Body):
    tool_id: Optional[UUID] = None
    step_title: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_required: Optional[bool] = None
    pass_score: Optional[int] = Field(default=None, ge=0, le=100)
    require_teacher_review: Optional[bool] = None
    is_active: Optional[bool] = None


class ReorderStepsRequest(BaseModel):
    step_ids: list[UUID] = Field(min_length=1)


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _require_owner(user: CurrentUser) -> None:
    if user.role != "owner":
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有教授可以修改模組與步驟")


def _migration_required() -> HTTPException:
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "MIGRATION_REQUIRED",
        "資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/010_module_management.sql",
    )


def _execute(query):
    """執行查詢；缺 migration 010 的欄位或函式時回 503，其他錯誤照常拋出。"""
    try:
        return query.execute()
    except APIError as e:
        if e.code in (UNDEFINED_COLUMN, FUNCTION_NOT_FOUND):
            raise _migration_required()
        raise


def _get_module_or_404(db, module_id: str) -> dict:
    res = db.table("learning_modules").select(MODULE_FIELDS).eq("id", module_id).limit(1).execute()
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "MODULE_NOT_FOUND", "找不到這個模組")
    return res.data[0]


def _load_steps(db, module_id: str) -> list[dict]:
    res = _execute(
        db.table("module_steps").select(STEP_FIELDS).eq("module_id", module_id).order("step_order")
    )
    return sorted(res.data or [], key=lambda s: s.get("step_order") or 0)


def _get_step_or_404(db, module_id: str, step_id: str) -> dict:
    for step in _load_steps(db, module_id):
        if step["id"] == step_id:
            return step
    raise _error(status.HTTP_404_NOT_FOUND, "STEP_NOT_FOUND", "這個模組裡沒有這個步驟")


def _attempt_counts(db, *, module_id: str) -> dict[str, int]:
    """每個步驟有幾筆學生作答（step_id → 筆數）"""
    res = db.table("step_attempts").select("step_id").eq("module_id", module_id).execute()
    counts: dict[str, int] = {}
    for row in res.data or []:
        counts[row["step_id"]] = counts.get(row["step_id"], 0) + 1
    return counts


def _load_tools(db, tool_ids: list[str]) -> dict[str, dict]:
    if not tool_ids:
        return {}
    res = db.table("ai_tools").select("id, title, status").in_("id", tool_ids).execute()
    return {t["id"]: t for t in res.data or []}


def _get_usable_tool(db, tool_id: str) -> dict:
    tool = _load_tools(db, [tool_id]).get(tool_id)
    if not tool:
        raise _error(status.HTTP_400_BAD_REQUEST, "TOOL_NOT_FOUND", "找不到這支機器人")
    if tool.get("status") == "archived":
        raise _error(status.HTTP_400_BAD_REQUEST, "TOOL_NOT_AVAILABLE", "這支機器人已封存，不能放進模組")
    return tool


def _step_output(step: dict, tools: dict[str, dict], counts: dict[str, int]) -> dict:
    tool = tools.get(step["tool_id"]) or {}
    attempts = counts.get(step["id"], 0)
    return {
        **step,
        "is_active": step.get("is_active", True),
        "tool_title": tool.get("title"),
        "tool_status": tool.get("status"),
        "attempt_count": attempts,
        # 有作答就不能刪除或換機器人，前端據此停用按鈕
        "can_delete": attempts == 0,
    }


def _module_detail(db, module: dict) -> dict:
    steps = _load_steps(db, module["id"])
    tools = _load_tools(db, sorted({s["tool_id"] for s in steps}))
    counts = _attempt_counts(db, module_id=module["id"])
    return {**module, "steps": [_step_output(s, tools, counts) for s in steps]}


def _check_publishable(db, module_id: str) -> None:
    active = [s for s in _load_steps(db, module_id) if s.get("is_active", True)]
    if not active:
        raise _error(status.HTTP_400_BAD_REQUEST, "MODULE_EMPTY", "模組至少要有一個啟用中的步驟才能發布")

    tools = _load_tools(db, sorted({s["tool_id"] for s in active}))
    unpublished = [
        (tools.get(s["tool_id"]) or {}).get("title") or s["step_title"]
        for s in active
        if (tools.get(s["tool_id"]) or {}).get("status") != "published"
    ]
    if unpublished:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "TOOL_NOT_PUBLISHED",
            f"這些步驟的機器人還沒發布，學生會看不到：{'、'.join(unpublished)}",
        )


def _compact_order(db, module_id: str) -> None:
    """刪除步驟後把順序重新編成 1、2、3…；失敗只記 log（留空號不影響運作）"""
    ids = [s["id"] for s in _load_steps(db, module_id)]
    if not ids:
        return
    try:
        db.rpc("reorder_module_steps", {"p_module_id": module_id, "p_step_ids": ids}).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"刪除步驟後重排順序失敗（是否尚未執行 migration 010？）：{e}")


# ──────────────────────────────────────────
# 模組
# ──────────────────────────────────────────

@router.get("")
async def list_modules(user: CurrentUser = Depends(require_staff)):
    """所有模組（含未發布），附啟用中與全部步驟數"""
    db = get_supabase()
    modules = db.table("learning_modules").select(MODULE_FIELDS).order("created_at").execute().data or []
    steps = _execute(db.table("module_steps").select("module_id, is_active")).data or []

    result = []
    for m in modules:
        own = [s for s in steps if s["module_id"] == m["id"]]
        result.append({
            **m,
            "step_count": len(own),
            "active_step_count": sum(1 for s in own if s.get("is_active", True)),
        })
    return {"data": result}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_module(body: CreateModuleRequest, user: CurrentUser = Depends(require_staff)):
    """建立模組；一律從未發布開始，排好步驟後再發布"""
    _require_owner(user)
    db = get_supabase()
    res = db.table("learning_modules").insert({
        "title": body.title,
        "description": body.description or None,
        "domain": body.domain,
        "is_published": False,
    }).execute()
    return {"data": {**res.data[0], "steps": []}, "message": "已建立模組，請新增步驟後再發布"}


@router.get("/{module_id}")
async def get_module(module_id: UUID, user: CurrentUser = Depends(require_staff)):
    """模組與全部步驟（含停用），每步附機器人名稱、作答筆數、能不能刪"""
    db = get_supabase()
    module = _get_module_or_404(db, str(module_id))
    return {"data": _module_detail(db, module)}


@router.patch("/{module_id}")
async def update_module(module_id: UUID, body: UpdateModuleRequest, user: CurrentUser = Depends(require_staff)):
    """修改模組資料或發布狀態"""
    _require_owner(user)
    db = get_supabase()
    module_id = str(module_id)
    _get_module_or_404(db, module_id)

    changes = body.model_dump(exclude_unset=True)
    if "description" in changes:
        changes["description"] = changes["description"] or None
    if not changes:
        raise _error(status.HTTP_400_BAD_REQUEST, "NO_CHANGES", "沒有要修改的欄位")
    if changes.get("is_published"):
        _check_publishable(db, module_id)

    db.table("learning_modules").update(changes).eq("id", module_id).execute()
    return {"data": _module_detail(db, _get_module_or_404(db, module_id))}


@router.delete("/{module_id}")
async def delete_module(module_id: UUID, user: CurrentUser = Depends(require_staff)):
    """刪除模組；已有學生作答就不能刪（請改成取消發布）"""
    _require_owner(user)
    db = get_supabase()
    module_id = str(module_id)
    _get_module_or_404(db, module_id)

    if _attempt_counts(db, module_id=module_id):
        raise _error(
            status.HTTP_409_CONFLICT,
            "MODULE_HAS_ATTEMPTS",
            "這個模組已有學生作答，刪除會連作答紀錄一起刪掉；請改成取消發布",
        )

    assigned = db.table("course_modules").select("course_id").eq("module_id", module_id).execute().data or []
    db.table("learning_modules").delete().eq("id", module_id).execute()
    message = "已刪除模組" + (f"，並取消 {len(assigned)} 門課程的指派" if assigned else "")
    return {"success": True, "message": message}


# ──────────────────────────────────────────
# 步驟
# ──────────────────────────────────────────

@router.post("/{module_id}/steps", status_code=status.HTTP_201_CREATED)
async def add_step(module_id: UUID, body: CreateStepRequest, user: CurrentUser = Depends(require_staff)):
    """在模組最後新增一個步驟"""
    _require_owner(user)
    db = get_supabase()
    module_id = str(module_id)
    _get_module_or_404(db, module_id)
    tool_id = str(body.tool_id)
    _get_usable_tool(db, tool_id)

    steps = _load_steps(db, module_id)
    next_order = max((s.get("step_order") or 0 for s in steps), default=0) + 1
    try:
        db.table("module_steps").insert({
            "module_id": module_id,
            "tool_id": tool_id,
            "step_order": next_order,
            "step_title": body.step_title,
            "is_required": body.is_required,
            "pass_score": body.pass_score,
            "require_teacher_review": body.require_teacher_review,
        }).execute()
    except APIError as e:
        if e.code == UNIQUE_VIOLATION:
            raise _error(status.HTTP_409_CONFLICT, "ORDER_CONFLICT", "步驟順序剛好被別人改了，請重新整理後再試")
        raise

    return {"data": _module_detail(db, _get_module_or_404(db, module_id))}


@router.patch("/{module_id}/steps/{step_id}")
async def update_step(
    module_id: UUID,
    step_id: UUID,
    body: UpdateStepRequest,
    user: CurrentUser = Depends(require_staff),
):
    """修改步驟；停用／啟用也走這裡（is_active）"""
    _require_owner(user)
    db = get_supabase()
    module_id, step_id = str(module_id), str(step_id)
    _get_module_or_404(db, module_id)
    step = _get_step_or_404(db, module_id, step_id)

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise _error(status.HTTP_400_BAD_REQUEST, "NO_CHANGES", "沒有要修改的欄位")

    if "tool_id" in changes:
        changes["tool_id"] = str(changes["tool_id"])
        if changes["tool_id"] != step["tool_id"]:
            if _attempt_counts(db, module_id=module_id).get(step_id):
                raise _error(
                    status.HTTP_409_CONFLICT,
                    "STEP_HAS_ATTEMPTS",
                    "這個步驟已有學生作答，不能更換機器人；請停用這一步，再新增一個步驟",
                )
            _get_usable_tool(db, changes["tool_id"])

    _execute(db.table("module_steps").update(changes).eq("id", step_id))
    return {"data": _module_detail(db, _get_module_or_404(db, module_id))}


@router.delete("/{module_id}/steps/{step_id}")
async def delete_step(module_id: UUID, step_id: UUID, user: CurrentUser = Depends(require_staff)):
    """刪除沒有作答的步驟；有作答的請改用停用"""
    _require_owner(user)
    db = get_supabase()
    module_id, step_id = str(module_id), str(step_id)
    _get_module_or_404(db, module_id)
    _get_step_or_404(db, module_id, step_id)

    if _attempt_counts(db, module_id=module_id).get(step_id):
        raise _error(
            status.HTTP_409_CONFLICT,
            "STEP_HAS_ATTEMPTS",
            "這個步驟已有學生作答，刪除會連作答紀錄一起刪掉；請改用停用",
        )

    db.table("module_steps").delete().eq("id", step_id).execute()
    _compact_order(db, module_id)
    return {"data": _module_detail(db, _get_module_or_404(db, module_id))}


@router.put("/{module_id}/steps/order")
async def reorder_steps(module_id: UUID, body: ReorderStepsRequest, user: CurrentUser = Depends(require_staff)):
    """依 step_ids 的順序重新編號 1、2、3…；必須剛好是這個模組的全部步驟"""
    _require_owner(user)
    db = get_supabase()
    module_id = str(module_id)
    _get_module_or_404(db, module_id)

    wanted = [str(s) for s in body.step_ids]
    current = {s["id"] for s in _load_steps(db, module_id)}
    if len(wanted) != len(set(wanted)) or set(wanted) != current:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_ORDER",
            "步驟清單和模組目前的步驟不一致，請重新整理後再試",
        )

    _execute(db.rpc("reorder_module_steps", {"p_module_id": module_id, "p_step_ids": wanted}))
    return {"data": _module_detail(db, _get_module_or_404(db, module_id))}
