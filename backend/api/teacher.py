"""
教師後台：AI 機器人管理

權限（docs/api.md 第四節）：
  - 列出、查看機器人：教師端（owner / assistant）；回傳含 system_prompt 等教學設計，學生不能看
  - 建立機器人：教師端（owner / assistant），建立者記為自己
  - 修改機器人：建立者本人或教授（owner），見 can_edit_tool()
  - 刪除機器人：只限 owner（教授）

版本（migration 019）：
  - 每次修改內容 ai_tools.version + 1，每一版的完整內容存一列在 ai_tool_versions，不覆蓋
  - 評分時 practice.py 把當時的 version 記在 ai_evaluations.tool_version，研究時對得上
  - 改評分規準不會重算舊的評分；回應的 attempt_count 讓前端提醒老師
"""

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from postgrest.exceptions import APIError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.auth import CurrentUser, require_staff
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teacher", tags=["Teacher Management"])

# PostgreSQL 外鍵違反：module_steps.tool_id 是 ON DELETE RESTRICT
FOREIGN_KEY_VIOLATION = "23503"

# 修改時會讓版本 + 1 的欄位（機器人的教學內容）；status 等狀態不在這裡
CONTENT_FIELDS = (
    "title",
    "domain",
    "target_competency",
    "role_instruction",
    "system_prompt",
    "teaching_strategy",
    "rubric_criteria",
    "scale_type",
    "error_taxonomy",
)
# 改了這些，舊的評分不會重算，要提醒老師
RUBRIC_FIELDS = ("rubric_criteria", "scale_type")

ScaleType = Literal["4_point", "5_point", "100_point", "pass_fail"]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def can_edit_tool(user: CurrentUser, tool: dict) -> bool:
    """
    誰可以修改這支機器人：建立者本人，或教授（owner）。
    待老師確認是否改成「所有老師都能改」，到時只要改這裡。
    """
    return user.role == "owner" or (bool(tool.get("created_by")) and tool.get("created_by") == user.id)


# ── Request Body ──────────────────────────────────────────────────────────


class RubricItem(BaseModel):
    # 允許額外欄位（例如教授自訂的 levels），修改時不會被丟掉
    model_config = ConfigDict(extra="allow")

    dimension: str = Field(min_length=1, max_length=100)
    max_score: float = Field(gt=0, le=1000)
    description: str = Field(default="", max_length=2000)

    @field_validator("dimension")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("評分項目不能空白")
        return v


TEXT_FIELDS = ("title", "domain", "target_competency", "role_instruction", "system_prompt")


class _ToolValidators(BaseModel):
    """建立與修改共用的檢查"""

    @field_validator(*TEXT_FIELDS, check_fields=False)
    @classmethod
    def _not_blank(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("不能空白")
        return v

    @field_validator("rubric_criteria", check_fields=False)
    @classmethod
    def _unique_dimensions(cls, v: list[RubricItem] | None) -> list[RubricItem] | None:
        if v is not None:
            names = [r.dimension for r in v]
            if len(names) != len(set(names)):
                raise ValueError("評分項目名稱不能重複")
        return v


class ToolCreate(_ToolValidators):
    title: str = Field(max_length=100)
    domain: str = Field(max_length=50)
    target_competency: str = Field(max_length=500)
    role_instruction: str = Field(max_length=2000)
    system_prompt: str = Field(max_length=20000)
    teaching_strategy: dict[str, Any] = Field(default_factory=dict)
    rubric_criteria: list[RubricItem] = Field(min_length=1, max_length=20)
    scale_type: ScaleType = "4_point"
    error_taxonomy: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    status: Literal["draft", "published"] = "published"


class ToolUpdate(_ToolValidators):
    """只送要改的欄位；沒送的維持原樣"""

    title: str | None = Field(default=None, max_length=100)
    domain: str | None = Field(default=None, max_length=50)
    target_competency: str | None = Field(default=None, max_length=500)
    role_instruction: str | None = Field(default=None, max_length=2000)
    system_prompt: str | None = Field(default=None, max_length=20000)
    teaching_strategy: dict[str, Any] | None = None
    rubric_criteria: list[RubricItem] | None = Field(default=None, min_length=1, max_length=20)
    scale_type: ScaleType | None = None
    error_taxonomy: list[dict[str, Any]] | None = Field(default=None, max_length=50)
    # 送出前看到的版本；別人在這之間改過就回 409，避免蓋掉別人的修改
    expected_version: int | None = None


# ── 共用 ────────────────────────────────────────────────────────────────────


def _get_tool(db, tool_id: str) -> dict:
    res = db.table("ai_tools").select("*").eq("id", tool_id).limit(1).execute()
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "TOOL_NOT_FOUND", "找不到該 AI 機器人")
    return res.data[0]


def _count_attempts(db, tool_id: str) -> int:
    """有多少筆學生作答用過這支機器人（經由模組步驟）"""
    steps = db.table("module_steps").select("id").eq("tool_id", tool_id).execute().data or []
    step_ids = [s["id"] for s in steps]
    if not step_ids:
        return 0
    res = db.table("step_attempts").select("id", count="exact").in_("step_id", step_ids).limit(1).execute()
    count = getattr(res, "count", None)
    if isinstance(count, int):
        return count
    # 計數不可用時（例如測試的記憶體資料庫）退回數列數
    return len(db.table("step_attempts").select("id").in_("step_id", step_ids).execute().data or [])


def _save_version(db, tool: dict, user_id: str | None) -> None:
    """把這一版的完整內容存進 ai_tool_versions；同一版已經存過就略過"""
    try:
        exists = (
            db.table("ai_tool_versions").select("id")
            .eq("tool_id", tool["id"]).eq("version", tool["version"]).limit(1).execute().data
        )
        if exists:
            return
        db.table("ai_tool_versions").insert({
            "tool_id": tool["id"],
            "version": tool["version"],
            "snapshot": tool,
            "edited_by": user_id,
        }).execute()
    except Exception:  # noqa: BLE001 - 版本紀錄失敗不擋住儲存，但一定要留下 log
        logger.exception("ai_tool_versions 寫入失敗（tool_id=%s, version=%s），請確認已執行 migration 019",
                         tool.get("id"), tool.get("version"))


def _dump(model: BaseModel, fields: tuple[str, ...]) -> dict:
    data = model.model_dump(include=set(fields), exclude_unset=True)
    if "rubric_criteria" in data and data["rubric_criteria"] is not None:
        # 配分是整數時存整數，和原本前端存的格式一致
        for r in data["rubric_criteria"]:
            if float(r["max_score"]).is_integer():
                r["max_score"] = int(r["max_score"])
    return data


# ── API ───────────────────────────────────────────────────────────────────


@router.get("/tools")
async def list_ai_tools(user: CurrentUser = Depends(require_staff)):
    """取得所有 AI 機器人清單；每支附 can_edit（目前登入者能不能修改）"""
    supabase = get_supabase()
    res = supabase.table("ai_tools").select("*").execute()
    tools = [{**t, "can_edit": can_edit_tool(user, t)} for t in (res.data or [])]
    # 依 docs/api.md 通用規範包成 {"data": ...}
    return {"data": tools}


@router.get("/tools/{tool_id}")
async def get_ai_tool(tool_id: str, user: CurrentUser = Depends(require_staff)):
    """單一機器人的完整設定（編輯頁用），附 can_edit 與 attempt_count（已有幾筆作答用過它）"""
    db = get_supabase()
    tool = _get_tool(db, tool_id)
    return {"data": {**tool, "can_edit": can_edit_tool(user, tool), "attempt_count": _count_attempts(db, tool_id)}}


@router.post("/tools", status_code=status.HTTP_201_CREATED)
async def create_ai_tool(body: ToolCreate, user: CurrentUser = Depends(require_staff)):
    """建立機器人（第 1 版），建立者記為目前登入者"""
    db = get_supabase()
    row = {**_dump(body, CONTENT_FIELDS), "status": body.status, "version": 1, "created_by": user.id}
    res = db.table("ai_tools").insert(row).execute()
    if not res.data:
        raise _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "CREATE_FAILED", "建立機器人失敗")
    tool = res.data[0]
    _save_version(db, tool, user.id)
    return {"data": tool}


@router.patch("/tools/{tool_id}")
async def update_ai_tool(tool_id: str, body: ToolUpdate, user: CurrentUser = Depends(require_staff)):
    """
    修改機器人內容：版本 + 1，舊版內容保留在 ai_tool_versions。
    內容沒有變就不升版。回應附 rubric_changed 與 attempt_count，讓前端提醒「舊的評分不會重算」。
    """
    db = get_supabase()
    tool = _get_tool(db, tool_id)

    if not can_edit_tool(user, tool):
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有建立者或教授可以修改這支機器人")

    current_version = tool.get("version") or 1
    if body.expected_version is not None and body.expected_version != current_version:
        raise _error(status.HTTP_409_CONFLICT, "VERSION_CONFLICT",
                     f"這支機器人已經被修改過（目前第 {current_version} 版），請重新整理後再改")

    changes = {k: v for k, v in _dump(body, CONTENT_FIELDS).items() if v is not None and v != tool.get(k)}
    if not changes:
        return {"data": tool, "changed": False, "rubric_changed": False, "attempt_count": 0}

    # 修改前先確認目前這一版有存檔（migration 019 之前建立、或上次存檔失敗的機器人）
    _save_version(db, tool, tool.get("updated_by") or tool.get("created_by"))

    new_version = current_version + 1
    res = (
        db.table("ai_tools")
        .update({
            **changes,
            "version": new_version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": user.id,
        })
        .eq("id", tool_id)
        .eq("version", current_version)  # 同時有兩個人在改時，只有一個會成功
        .execute()
    )
    if not res.data:
        raise _error(status.HTTP_409_CONFLICT, "VERSION_CONFLICT", "這支機器人剛被別人修改過，請重新整理後再改")

    updated = res.data[0]
    _save_version(db, updated, user.id)

    rubric_changed = any(k in changes for k in RUBRIC_FIELDS)
    return {
        "data": updated,
        "changed": True,
        "rubric_changed": rubric_changed,
        "attempt_count": _count_attempts(db, tool_id) if rubric_changed else 0,
    }


@router.delete("/tools/{tool_id}")
async def delete_ai_tool(tool_id: str, user: CurrentUser = Depends(require_staff)):
    """刪除指定的 AI 機器人（只限教授；助教可以看但不能刪）"""
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PERMISSION_DENIED", "message": "只有教授可以刪除機器人"},
        )

    supabase = get_supabase()

    # 執行刪除
    try:
        res = supabase.table("ai_tools").delete().eq("id", tool_id).execute()
    except APIError as e:
        # 機器人還被某個模組步驟使用時，資料庫會拒絕刪除；回 409 而不是 500
        if e.code == FOREIGN_KEY_VIOLATION:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TOOL_IN_USE",
                    "message": "這個機器人仍被課程模組的步驟使用，請先從模組移除或改為封存",
                },
            )
        raise

    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到該 AI 機器人或已被刪除"
        )

    return {"success": True, "message": "已成功刪除 AI 機器人"}
