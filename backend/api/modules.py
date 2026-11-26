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
複製模組（#89）：POST /api/modules/{id}/copy，規則在 core/templates.py。
"""

import logging
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError
from pydantic import BaseModel, ConfigDict, Field

from core.audit import record
from core.auth import CurrentUser, require_staff
from core.templates import copy_title, module_content, pick_title
from core.templates import create_module as create_module_from_content
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
    "pass_score, require_teacher_review, is_active, pass_forward_keys"
)
# migration 019 才有的欄位：還沒跑時讀步驟照常運作（當作 score），只有要設定它時才回 503
STEP_FIELDS_019 = STEP_FIELDS + ", pass_mode"
# migration 020：聊天室關卡
STEP_FIELDS_020 = STEP_FIELDS_019 + ", step_type"
# migration 024：停在機器人的第幾版（NULL = 跟著最新版，#89）
STEP_FIELDS_024 = STEP_FIELDS_020 + ", tool_version"
STEP_TYPES = ("answer", "chat")
PASS_MODES = ("score", "complete", "teacher_review")

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


class ForwardFieldItem(_Body):
    """要傳給後面關卡的一個欄位（#84）"""
    label: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=200)


class CreateStepRequest(_Body):
    tool_id: UUID
    step_title: str = Field(min_length=1, max_length=100)
    is_required: bool = True
    pass_score: int = Field(default=70, ge=0, le=100)
    require_teacher_review: bool = False
    # 怎麼算通過（#65）：score 看分數、complete 作答即通過、teacher_review 教師複核後通過
    pass_mode: Literal["score", "complete", "teacher_review"] = "score"
    # answer = 原本的作答關卡；chat = 多機器人聊天室（#68），tool_id 是聊天室的評分機器人
    step_type: Literal["answer", "chat"] = "chat"  # 一個模組一個聊天室（#108）：預設聊天室階段
    # 通過時要抽出、傳給後面關卡的欄位（#84；存在 module_steps.pass_forward_keys）
    pass_forward_keys: list[ForwardFieldItem] = Field(default_factory=list, max_length=10)


class UpdateStepRequest(_Body):
    tool_id: Optional[UUID] = None
    step_title: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_required: Optional[bool] = None
    pass_score: Optional[int] = Field(default=None, ge=0, le=100)
    require_teacher_review: Optional[bool] = None
    is_active: Optional[bool] = None
    pass_mode: Optional[Literal["score", "complete", "teacher_review"]] = None
    step_type: Optional[Literal["answer", "chat"]] = None
    pass_forward_keys: Optional[list[ForwardFieldItem]] = Field(default=None, max_length=10)


class ChatBotItem(_Body):
    tool_id: UUID
    display_name: str = Field(min_length=1, max_length=30)
    when_to_use: str = Field(default="", max_length=300)
    # 學習助教（#67）：回答前先查這門課的教材與學生自己的作答紀錄（需要 migration 021）
    use_materials: bool = False


class ChatBotsRequest(BaseModel):
    bots: list[ChatBotItem] = Field(max_length=8)


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


def _migration_required(migration: str = "010_module_management.sql") -> HTTPException:
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "MIGRATION_REQUIRED",
        f"資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/{migration}",
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
    def query(fields: str):
        return db.table("module_steps").select(fields).eq("module_id", module_id).order("step_order")

    # 新欄位依 migration 逐層退回：沒跑 024 就不讀 tool_version、沒跑 020 就不讀 step_type、沒跑 019 就不讀 pass_mode
    res = None
    for fields, column in (
        (STEP_FIELDS_024, "tool_version"),
        (STEP_FIELDS_020, "step_type"),
        (STEP_FIELDS_019, "pass_mode"),
    ):
        try:
            res = query(fields).execute()
            break
        except APIError as e:
            if e.code != UNDEFINED_COLUMN or column not in str(e.message):
                raise
    if res is None:
        res = _execute(query(STEP_FIELDS))
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
        "pass_mode": step.get("pass_mode") if step.get("pass_mode") in PASS_MODES else "score",
        "step_type": step.get("step_type") if step.get("step_type") in STEP_TYPES else "answer",
        # 停在機器人的第幾版（修改機器人時選的，#89）；None = 跟著最新版
        "tool_version": step.get("tool_version"),
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
    record(db, user, "module.create", target_type="module", target_id=res.data[0]["id"], detail={"title": body.title})
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
    record(db, user, "module.update", target_type="module", target_id=module_id, detail=changes)
    return {"data": _module_detail(db, _get_module_or_404(db, module_id))}


@router.delete("/{module_id}")
async def delete_module(module_id: UUID, user: CurrentUser = Depends(require_staff)):
    """刪除模組；已有學生作答就不能刪（請改成取消發布）"""
    _require_owner(user)
    db = get_supabase()
    module_id = str(module_id)
    module = _get_module_or_404(db, module_id)

    if _attempt_counts(db, module_id=module_id):
        raise _error(
            status.HTTP_409_CONFLICT,
            "MODULE_HAS_ATTEMPTS",
            "這個模組已有學生作答，刪除會連作答紀錄一起刪掉；請改成取消發布",
        )

    assigned = db.table("course_modules").select("course_id").eq("module_id", module_id).execute().data or []
    db.table("learning_modules").delete().eq("id", module_id).execute()
    message = "已刪除模組" + (f"，並取消 {len(assigned)} 門課程的指派" if assigned else "")
    record(db, user, "module.delete", target_type="module", target_id=module_id,
           detail={"title": module.get("title"), "unassigned_courses": [a["course_id"] for a in assigned]})
    return {"success": True, "message": message}


class CopyModuleRequest(_Body):
    """新的名稱（選填）；不填就是「原本的名稱（複本）」"""
    title: Optional[str] = Field(default=None, max_length=100)


def load_module_content(db, module: dict) -> dict:
    """模組要複製或存成範本的內容（core/templates.module_content），範本 API 也用這個"""
    from core.chat_room import migration_missing

    steps = _load_steps(db, module["id"])
    bots: list[dict] = []
    chat_step_ids = [s["id"] for s in steps if s.get("step_type") == "chat"]
    if chat_step_ids:
        try:
            bots = db.table("chat_room_bots").select("*").in_("step_id", chat_step_ids).execute().data or []
        except Exception as e:  # noqa: BLE001
            if not migration_missing(e):
                raise
    # 整個模組共用的對話機器人（#108）；還沒跑 030 時沒有
    try:
        shared = db.table("chat_room_bots").select("*").eq("module_id", module["id"]).execute().data or []
        bots += [{**b, "step_id": None} for b in shared if not b.get("step_id")]
    except Exception as e:  # noqa: BLE001
        if not (migration_missing(e) or "module_id" in str(e)):
            raise
    return module_content(module, steps, bots)


def copy_result(module: dict, skipped: list[str], db) -> dict:
    """複製或從範本建立模組的回應：新模組（含關卡）＋略過了哪些關卡"""
    message = f"已建立「{module['title']}」（未發布）"
    if skipped:
        message += f"；這些關卡的機器人已封存或刪除，沒有複製：{'、'.join(skipped)}"
    return {"data": _module_detail(db, module), "skipped_steps": skipped, "message": message}


@router.post("/{module_id}/copy", status_code=status.HTTP_201_CREATED)
async def copy_module(
    module_id: UUID,
    body: Optional[CopyModuleRequest] = None,
    user: CurrentUser = Depends(require_staff),
):
    """
    複製模組（#89）：啟用中的關卡（含聊天室的對話機器人）照順序複製成一個新的、未發布的模組。
    關卡指向原本的機器人，不會連機器人一起複製；學生作答與課程指派都不會複製。
    """
    _require_owner(user)
    db = get_supabase()
    source = _get_module_or_404(db, str(module_id))
    content = load_module_content(db, source)
    title = pick_title(body.title if body else None, copy_title(source.get("title")))
    module, skipped = create_module_from_content(db, content, title=title)
    record(db, user, "module.copy", target_type="module", target_id=module["id"],
           detail={"source_id": source["id"], "skipped_steps": skipped})
    return copy_result(module, skipped, db)


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
            # 預設值不寫：還沒跑 019 的資料庫照樣能新增步驟
            **({"pass_mode": body.pass_mode} if body.pass_mode != "score" else {}),
            **({"step_type": body.step_type} if body.step_type != "answer" else {}),
            **({"pass_forward_keys": [f.model_dump() for f in body.pass_forward_keys]} if body.pass_forward_keys else {}),
        }).execute()
    except APIError as e:
        if e.code == UNIQUE_VIOLATION:
            raise _error(status.HTTP_409_CONFLICT, "ORDER_CONFLICT", "步驟順序剛好被別人改了，請重新整理後再試")
        if e.code == UNDEFINED_COLUMN and "pass_mode" in str(e.message):
            raise _migration_required("019_step_pass_mode.sql")
        if e.code == UNDEFINED_COLUMN and "step_type" in str(e.message):
            raise _migration_required("020_chat_rooms.sql")
        raise

    record(db, user, "step.create", target_type="module", target_id=module_id,
           detail={"step_title": body.step_title, "tool_id": tool_id, "step_order": next_order})
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
            # 換了機器人：原本停在舊機器人第幾版的設定不適用了，改成跟著新機器人的最新版
            if step.get("tool_version") is not None:
                changes["tool_version"] = None

    try:
        _execute(db.table("module_steps").update(changes).eq("id", step_id))
    except HTTPException:
        if "step_type" in changes:
            raise _migration_required("020_chat_rooms.sql")
        if "pass_mode" in changes:
            raise _migration_required("019_step_pass_mode.sql")
        raise
    record(db, user, "step.update", target_type="step", target_id=step_id,
           detail={"module_id": module_id, "changes": changes})
    return {"data": _module_detail(db, _get_module_or_404(db, module_id))}


@router.delete("/{module_id}/steps/{step_id}")
async def delete_step(module_id: UUID, step_id: UUID, user: CurrentUser = Depends(require_staff)):
    """刪除沒有作答的步驟；有作答的請改用停用"""
    _require_owner(user)
    db = get_supabase()
    module_id, step_id = str(module_id), str(step_id)
    _get_module_or_404(db, module_id)
    step = _get_step_or_404(db, module_id, step_id)

    if _attempt_counts(db, module_id=module_id).get(step_id):
        raise _error(
            status.HTTP_409_CONFLICT,
            "STEP_HAS_ATTEMPTS",
            "這個步驟已有學生作答，刪除會連作答紀錄一起刪掉；請改用停用",
        )

    db.table("module_steps").delete().eq("id", step_id).execute()
    _compact_order(db, module_id)
    record(db, user, "step.delete", target_type="step", target_id=step_id,
           detail={"module_id": module_id, "step_title": step.get("step_title"), "tool_id": step.get("tool_id")})
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


# ──────────────────────────────────────────
# 聊天室的機器人（#68，migration 020）
# ──────────────────────────────────────────

@router.get("/{module_id}/steps/{step_id}/chat-bots")
async def get_chat_bots(module_id: UUID, step_id: UUID, user: CurrentUser = Depends(require_staff)):
    """聊天室關卡的對話機器人（評分機器人是步驟本身的 tool_id，不在清單裡）"""
    db = get_supabase()
    module_id, step_id = str(module_id), str(step_id)
    _get_module_or_404(db, module_id)
    _get_step_or_404(db, module_id, step_id)
    return {"data": _chat_bot_rows(db, step_id)}


@router.put("/{module_id}/steps/{step_id}/chat-bots")
async def replace_chat_bots(
    module_id: UUID,
    step_id: UUID,
    body: ChatBotsRequest,
    user: CurrentUser = Depends(require_staff),
):
    """
    整份換掉聊天室的對話機器人（新增、刪除、改名、改出場時機、排序都走這裡）。
    已有的對話不受影響：chat_messages 只記當時回答的機器人名稱。
    """
    _require_owner(user)
    db = get_supabase()
    module_id, step_id = str(module_id), str(step_id)
    _get_module_or_404(db, module_id)
    step = _get_step_or_404(db, module_id, step_id)

    evaluator = _load_tools(db, [step["tool_id"]]).get(step["tool_id"]) or {}
    names, tool_ids = set(), set()
    for item in body.bots:
        tool_id = str(item.tool_id)
        if tool_id == step["tool_id"]:
            raise _error(status.HTTP_400_BAD_REQUEST, "EVALUATOR_IN_LIST",
                         "評分機器人已經在聊天室裡（關卡的機器人），不用再加一次")
        if tool_id in tool_ids:
            raise _error(status.HTTP_400_BAD_REQUEST, "DUPLICATE_TOOL", "同一支機器人只能加一次")
        key = item.display_name.lower()
        if key in names or key == (evaluator.get("title") or "").strip().lower():
            raise _error(status.HTTP_400_BAD_REQUEST, "DUPLICATE_NAME",
                         f"名稱「{item.display_name}」重複了：學生用 @名稱 指名，名稱不能一樣")
        _get_usable_tool(db, tool_id)
        names.add(key)
        tool_ids.add(tool_id)

    # 原本停在舊版的對話機器人（修改機器人時選的，#89），整份換掉時保留
    locked = {r["tool_id"]: r["tool_version"] for r in _chat_bot_rows(db, step_id) if r.get("tool_version")}

    try:
        db.table("chat_room_bots").delete().eq("step_id", step_id).execute()
        if body.bots:
            db.table("chat_room_bots").insert([
                {"step_id": step_id, "tool_id": str(item.tool_id), "display_name": item.display_name,
                 "when_to_use": item.when_to_use, "sort_order": i + 1,
                 # 預設值不寫：還沒跑 021 的資料庫照樣能存
                 **({"use_materials": True} if item.use_materials else {}),
                 **({"tool_version": locked[str(item.tool_id)]} if str(item.tool_id) in locked else {})}
                for i, item in enumerate(body.bots)
            ]).execute()
    except APIError as e:
        if e.code == UNDEFINED_COLUMN and "use_materials" in str(e.message):
            raise _migration_required("021_course_materials.sql")
        if e.code in (UNDEFINED_COLUMN, "42P01", "PGRST205"):
            raise _migration_required("020_chat_rooms.sql")
        raise
    return {"data": _chat_bot_rows(db, step_id)}


def _chat_bot_rows(db, step_id: str) -> list[dict]:
    from core.chat_room import load_bot_rows, migration_missing

    try:
        return load_bot_rows(db, step_id)
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _migration_required("020_chat_rooms.sql")
        raise


# ──────────────────────────────────────────
# 整個模組共用的對話機器人（#108，migration 030）
# ──────────────────────────────────────────

@router.get("/{module_id}/chat-bots")
async def get_module_chat_bots(module_id: UUID, user: CurrentUser = Depends(require_staff)):
    """模組聊天室每個階段都會出現的對話機器人（例如學習助教）；只在某個階段出現的設定在該階段"""
    from core.chat_room import load_bot_rows

    db = get_supabase()
    module_id = str(module_id)
    _get_module_or_404(db, module_id)
    return {"data": load_bot_rows(db, module_id, column="module_id")}


@router.put("/{module_id}/chat-bots")
async def replace_module_chat_bots(module_id: UUID, body: ChatBotsRequest, user: CurrentUser = Depends(require_staff)):
    """
    整份換掉模組共用的對話機器人。名稱不能和任何階段的評分機器人一樣（學生用 @名稱 指名）；
    和某個階段自己的對話機器人重複時，那個階段以自己的設定為準。
    """
    from core.chat_room import load_bot_rows

    _require_owner(user)
    db = get_supabase()
    module_id = str(module_id)
    _get_module_or_404(db, module_id)

    steps = _load_steps(db, module_id)
    evaluators = _load_tools(db, sorted({s["tool_id"] for s in steps if s.get("tool_id")}))
    evaluator_names = {(t.get("title") or "").strip().lower() for t in evaluators.values()}
    names, tool_ids = set(), set()
    for item in body.bots:
        tool_id = str(item.tool_id)
        if tool_id in evaluators:
            raise _error(status.HTTP_400_BAD_REQUEST, "EVALUATOR_IN_LIST",
                         "這支是某個階段的評分機器人，已經在聊天室裡了，不用再加成共用的")
        if tool_id in tool_ids:
            raise _error(status.HTTP_400_BAD_REQUEST, "DUPLICATE_TOOL", "同一支機器人只能加一次")
        key = item.display_name.lower()
        if key in names or key in evaluator_names:
            raise _error(status.HTTP_400_BAD_REQUEST, "DUPLICATE_NAME",
                         f"名稱「{item.display_name}」重複了：學生用 @名稱 指名，名稱不能一樣")
        _get_usable_tool(db, tool_id)
        names.add(key)
        tool_ids.add(tool_id)

    current = load_bot_rows(db, module_id, column="module_id")
    locked = {r["tool_id"]: r["tool_version"] for r in current if r.get("tool_version")}
    try:
        db.table("chat_room_bots").delete().eq("module_id", module_id).execute()
        if body.bots:
            db.table("chat_room_bots").insert([
                {"module_id": module_id, "tool_id": str(item.tool_id), "display_name": item.display_name,
                 "when_to_use": item.when_to_use, "sort_order": i + 1,
                 **({"use_materials": True} if item.use_materials else {}),
                 **({"tool_version": locked[str(item.tool_id)]} if str(item.tool_id) in locked else {})}
                for i, item in enumerate(body.bots)
            ]).execute()
    except APIError as e:
        # 還沒跑 030：沒有 module_id 欄位，或 step_id 還是 NOT NULL（23502）
        if e.code == "23502" or (e.code == UNDEFINED_COLUMN and "module_id" in str(e.message)):
            raise _migration_required("030_module_chat_room.sql")
        raise
    record(db, user, "module.chat_bots", target_type="module", target_id=module_id,
           detail={"bots": [item.display_name for item in body.bots]})
    return {"data": load_bot_rows(db, module_id, column="module_id")}
