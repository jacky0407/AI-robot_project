from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from database.client import get_supabase

router = APIRouter(prefix="/api/teacher", tags=["Teacher Management"])


@router.get("/tools")
async def list_ai_tools():
    """取得所有 AI 機器人清單"""
    supabase = get_supabase()
    res = supabase.table("ai_tools").select("*").execute()
    return res.data

@router.delete("/tools/{tool_id}")
async def delete_ai_tool(tool_id: str):
    """刪除指定的 AI 機器人"""
    supabase = get_supabase()
    
    # 執行刪除
    res = supabase.table("ai_tools").delete().eq("id", tool_id).execute()
    
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到該 AI 機器人或已被刪除"
        )
    
    return {"success": True, "message": "已成功刪除 AI 機器人"}