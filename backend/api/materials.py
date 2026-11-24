"""
課程教材 API（學習助教 RAG，#67，migration 021）— 限教師端

  GET    /api/courses/{course_id}/materials                         教材清單
  POST   /api/courses/{course_id}/materials                         新增（貼上文字）
  POST   /api/courses/{course_id}/materials/upload                  上傳檔案（pdf、docx、pptx、txt、md，#66）
  GET    /api/courses/{course_id}/materials/{material_id}/chunks    看切段結果
  DELETE /api/courses/{course_id}/materials/{material_id}           刪除（切段一起刪）

上傳的檔案只取出文字（core/file_text.py），原始檔不保存；取出的文字走同一個 add_material()。
新增時先過個資檢查（core/privacy.py），有個資整份不存、請老師修改。
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from core.ai.llm_keys import use_api_key
from core.auth import CurrentUser, require_staff
from core.chat_room import migration_missing
from core.file_text import MAX_FILE_BYTES, FileTextError, extract_text
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
    return await _save(db, course_id, body.title, body.content, user, "text")


@router.post("/{course_id}/materials/upload", status_code=status.HTTP_201_CREATED)
async def upload_material(
    course_id: UUID,
    file: UploadFile = File(...),
    title: str = Form(default="", max_length=100),
    user: CurrentUser = Depends(require_staff),
):
    """
    上傳 pdf、docx、pptx、txt、md：取出文字 → 個資檢查 → 切段 → 向量。
    只依檔案內容判斷格式；原始檔不保存（教材庫只需要文字）。
    """
    db = get_supabase()
    course_id = str(course_id)
    _course_or_404(db, course_id)

    data = await file.read(MAX_FILE_BYTES + 1)
    filename = file.filename or ""
    try:
        text, source_type = await run_in_threadpool(extract_text, filename, data)
    except FileTextError as e:
        raise _error(status.HTTP_400_BAD_REQUEST, e.code, e.message)

    name = title.strip() or filename.rsplit(".", 1)[0].strip() or "未命名教材"
    return await _save(db, course_id, name[:100], text, user, source_type)


async def _save(db, course_id: str, title: str, text: str, user: CurrentUser, source_type: str) -> dict:
    try:
        with use_api_key(None):  # 教材向量一律用平台金鑰
            material = await add_material(
                db, course_id=course_id, title=title, text=text, created_by=user.id, source_type=source_type
            )
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
