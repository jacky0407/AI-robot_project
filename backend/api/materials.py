"""
課程教材 API（學習助教 RAG，#67，migration 021）— 限教師端

  GET    /api/courses/{course_id}/materials                         教材清單
  POST   /api/courses/{course_id}/materials                         新增（貼上文字或 .txt/.md 的內容）
  GET    /api/courses/{course_id}/materials/{material_id}/chunks    看切段結果
  DELETE /api/courses/{course_id}/materials/{material_id}           刪除（切段一起刪）

pdf／docx／pptx 的取文字由 #66（檔案上傳）負責，取出的文字走同一個 add_material()。
新增時先過個資檢查（core/privacy.py），有個資整份不存、請老師修改。
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from core.ai.llm_keys import use_api_key
from core.auth import CurrentUser, require_staff
from core.chat_room import migration_missing
from core.materials import MAX_MATERIAL_CHARS, MaterialError, add_material
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/courses", tags=["materials"])

MATERIAL_FIELDS = "id, course_id, title, source_type, char_count, chunk_count, created_at"


class CreateMaterialRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=MAX_MATERIAL_CHARS)


def _error(status_code: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def _migration_required() -> HTTPException:
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "MIGRATION_REQUIRED",
                  "資料庫還沒更新：請在 Supabase SQL Editor 執行 supabase/migrations/021_course_materials.sql")


def _course_or_404(db, course_id: str) -> dict:
    res = db.table("courses").select("id").eq("id", course_id).limit(1).execute()
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")
    return res.data[0]


def _run(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _migration_required()
        raise


@router.get("/{course_id}/materials")
async def list_materials(course_id: UUID, user: CurrentUser = Depends(require_staff)):
    db = get_supabase()
    course_id = str(course_id)
    _course_or_404(db, course_id)
    res = _run(lambda: db.table("course_materials").select(MATERIAL_FIELDS).eq("course_id", course_id)
               .order("created_at", desc=True).execute())
    return {"data": res.data or []}


@router.post("/{course_id}/materials", status_code=status.HTTP_201_CREATED)
async def create_material(course_id: UUID, body: CreateMaterialRequest, user: CurrentUser = Depends(require_staff)):
    db = get_supabase()
    course_id = str(course_id)
    _course_or_404(db, course_id)
    try:
        with use_api_key(None):  # 教材向量一律用平台金鑰
            material = await add_material(db, course_id=course_id, title=body.title, text=body.content, created_by=user.id)
    except MaterialError as e:
        raise _error(status.HTTP_400_BAD_REQUEST, e.code, e.message, **e.detail)
    except Exception as e:  # noqa: BLE001
        if migration_missing(e):
            raise _migration_required()
        logger.error(f"新增教材失敗：{e}")
        raise _error(status.HTTP_502_BAD_GATEWAY, "EMBEDDING_FAILED", "教材處理失敗（AI 向量服務沒有回應），請稍後再試")
    return {"data": {k: material.get(k) for k in MATERIAL_FIELDS.split(", ")}}


@router.get("/{course_id}/materials/{material_id}/chunks")
async def list_chunks(course_id: UUID, material_id: UUID, user: CurrentUser = Depends(require_staff)):
    db = get_supabase()
    res = _run(lambda: db.table("material_chunks").select("chunk_index, content")
               .eq("course_id", str(course_id)).eq("material_id", str(material_id)).order("chunk_index").execute())
    return {"data": res.data or []}


@router.delete("/{course_id}/materials/{material_id}")
async def delete_material(course_id: UUID, material_id: UUID, user: CurrentUser = Depends(require_staff)):
    db = get_supabase()
    res = _run(lambda: db.table("course_materials").delete()
               .eq("course_id", str(course_id)).eq("id", str(material_id)).execute())
    if not res.data:
        raise _error(status.HTTP_404_NOT_FOUND, "MATERIAL_NOT_FOUND", "找不到這份教材")
    return {"success": True, "message": "已刪除教材"}
