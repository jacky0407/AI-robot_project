"""
範本 API（#89，需求說明書 BLD-002、BLD-008，migration 023）

範本另外存在 templates 表，跟正式的機器人、模組分開；學生看不到。
存的是「存成範本當時的內容副本」，原本的機器人或模組之後被修改或刪除，範本都不受影響。

  GET    /api/templates?kind=tool|module   範本清單（教師端）
  POST   /api/templates                    把一支機器人或一個模組存成範本
  POST   /api/templates/{id}/use           從範本建立新的機器人（草稿、第 1 版）或模組（未發布）
  DELETE /api/templates/{id}               刪除範本（建立者或教授）；用範本建立過的機器人、模組不受影響

權限（跟機器人、模組本身一致）：
  - 機器人範本：教師端（owner / assistant）都可以存、可以用
  - 模組範本：只限教授（owner），因為模組的新增、修改只限教授（api/modules.py）

複製與建立的規則在 core/templates.py。
"""

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from api.modules import _get_module_or_404, copy_result, load_module_content
from api.teacher import _get_tool
from core.auth import CurrentUser, require_staff
from core.templates import (
    TemplateError,
    check_tool_content,
    create_module,
    create_tool,
    pick_title,
    tool_content,
)
from database.client import get_supabase

router = APIRouter(prefix="/api/templates", tags=["Templates"])

# 還沒跑 migration 023（沒有 templates 表）
MISSING_TABLE_CODES = ("42P01", "PGRST205")

LIST_FIELDS = "id, kind, title, description, content, source_id, source_version, created_by, created_at"

Kind = Literal["tool", "module"]


class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class SaveTemplateRequest(_Body):
    kind: Kind
    source_id: UUID
    # 不填就用機器人或模組目前的名稱
    title: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)


class UseTemplateRequest(_Body):
    # 新的機器人或模組名稱；不填就用範本的名稱
    title: Optional[str] = Field(default=None, max_length=100)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _execute(query):
    """執行查詢；還沒有 templates 表時回 503 告訴老師要跑哪一支 migration"""
    try:
        return query.execute()
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) in MISSING_TABLE_CODES:
            raise _error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "MIGRATION_REQUIRED",
                "資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/023_templates.sql",
            )
        raise


def _require_kind_permission(user: CurrentUser, kind: str) -> None:
    if kind == "module" and user.role != "owner":
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有教授可以存模組範本或用模組範本建立模組")


def can_delete_template(user: CurrentUser, template: dict) -> bool:
    """建立者本人或教授可以刪除範本"""
    return user.role == "owner" or (bool(template.get("created_by")) and template.get("created_by") == user.id)


def _get_template(db, template_id: str) -> dict:
    res = _execute(db.table("templates").select(LIST_FIELDS).eq("id", template_id).limit(1))
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "TEMPLATE_NOT_FOUND", "找不到這個範本")
    return res.data[0]


def _output(user: CurrentUser, template: dict) -> dict:
    return {**template, "can_delete": can_delete_template(user, template)}


# ── API ───────────────────────────────────────────────────────────────────


@router.get("")
async def list_templates(kind: Optional[Kind] = Query(default=None), user: CurrentUser = Depends(require_staff)):
    """範本清單（新的在前），每個附 can_delete；含 content，建立機器人時可以從範本匯入評分規準"""
    db = get_supabase()
    query = db.table("templates").select(LIST_FIELDS)
    if kind:
        query = query.eq("kind", kind)
    rows = _execute(query.order("created_at", desc=True)).data or []
    return {"data": [_output(user, t) for t in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def save_template(body: SaveTemplateRequest, user: CurrentUser = Depends(require_staff)):
    """把一支機器人或一個模組「現在的內容」存成範本"""
    _require_kind_permission(user, body.kind)
    db = get_supabase()
    source_id = str(body.source_id)

    if body.kind == "tool":
        source = _get_tool(db, source_id)
        content = tool_content(source)
        source_version = source.get("version")
    else:
        source = _get_module_or_404(db, source_id)
        content = load_module_content(db, source)
        source_version = None

    row = {
        "kind": body.kind,
        "title": pick_title(body.title, source.get("title") or "未命名範本"),
        "description": body.description or None,
        "content": content,
        "source_id": source_id,
        "source_version": source_version,
        "created_by": user.id,
    }
    template = _execute(db.table("templates").insert(row)).data[0]
    return {"data": _output(user, template), "message": f"已存成範本「{template['title']}」"}


@router.post("/{template_id}/use", status_code=status.HTTP_201_CREATED)
async def use_template(
    template_id: UUID,
    body: Optional[UseTemplateRequest] = None,
    user: CurrentUser = Depends(require_staff),
):
    """從範本建立：機器人 → 新的一支（第 1 版、草稿、建立者是自己）；模組 → 新的未發布模組"""
    db = get_supabase()
    template = _get_template(db, str(template_id))
    _require_kind_permission(user, template["kind"])

    content = template.get("content") or {}
    fallback = (content.get("title") if isinstance(content, dict) else None) or template["title"]
    title = pick_title(body.title if body else None, fallback)

    try:
        if template["kind"] == "tool":
            tool = create_tool(db, check_tool_content(content), user_id=user.id, title=title)
            return {"data": {**tool, "can_edit": True}, "kind": "tool",
                    "message": f"已從範本建立「{tool['title']}」（草稿），請確認內容後再發布"}

        module, skipped = create_module(db, content, title=title)
        return {**copy_result(module, skipped, db), "kind": "module"}
    except TemplateError as e:
        raise _error(status.HTTP_400_BAD_REQUEST, e.code, e.message)


@router.delete("/{template_id}")
async def delete_template(template_id: UUID, user: CurrentUser = Depends(require_staff)):
    """刪除範本（建立者或教授）；用這個範本建立過的機器人、模組都不受影響"""
    db = get_supabase()
    template = _get_template(db, str(template_id))
    if not can_delete_template(user, template):
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有建立者或教授可以刪除這個範本")
    _execute(db.table("templates").delete().eq("id", template["id"]))
    return {"success": True, "message": f"已刪除範本「{template['title']}」"}
