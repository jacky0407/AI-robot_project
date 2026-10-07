from fastapi import APIRouter, HTTPException, status
from postgrest.exceptions import APIError
from database.client import get_supabase

router = APIRouter(prefix="/api/teacher", tags=["Teacher Management"])

# PostgreSQL 外鍵違反：module_steps.tool_id 是 ON DELETE RESTRICT
FOREIGN_KEY_VIOLATION = "23503"


@router.get("/tools")
async def list_ai_tools():
    """取得所有 AI 機器人清單"""
    supabase = get_supabase()
    res = supabase.table("ai_tools").select("*").execute()
    # 依 docs/api.md 通用規範包成 {"data": ...}
    return {"data": res.data or []}

@router.delete("/tools/{tool_id}")
async def delete_ai_tool(tool_id: str):
    """刪除指定的 AI 機器人"""
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
