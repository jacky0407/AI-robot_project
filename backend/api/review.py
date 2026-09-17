from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from database.client import get_supabase
from datetime import datetime, timezone

router = APIRouter(prefix="/api/review", tags=["review"])

class DimensionOverride(BaseModel):
    dimension: str
    teacher_score: int
    override_reason: str

class JudgeRequest(BaseModel):
    action: str  # accept, modify, reject
    dimension_overrides: List[DimensionOverride] = []
    teacher_comment: str = ""
    require_redo: bool = False

@router.get("/pending")
async def get_pending_reviews(
    course_id: Optional[str] = Query(None),
    tool_id: Optional[str] = Query(None),
    needs_attention: Optional[bool] = Query(None)
):
    """
    從 Supabase 讀取 ai_evaluations JOIN step_attempts JOIN profiles
    回傳等待複核的清單（ai_evaluations.teacher_review_id IS NULL）
    """
    db = get_supabase()
    
    # Supabase doesn't support complex JOINs with filtering easily via the JS/Python client in a single clean query sometimes,
    # but we can try selecting nested fields.
    # Actually, we should select from ai_evaluations and inner join step_attempts.
    # query = db.table("ai_evaluations").select("*, step_attempts(*, profiles(*))").is_("teacher_review_id", "null")
    # For now, let's keep it simple as requested.
    query = db.table("ai_evaluations").select(
        "id, attempt_id, total_score, step_attempts(id, created_at, user_input_content, user_id, profiles(display_name))"
    ).is_("teacher_review_id", "null")
    
    res = query.execute()
    
    # Format according to docs
    data = []
    for item in res.data:
        attempt = item.get("step_attempts")
        if not attempt:
            continue
            
        profile = attempt.get("profiles", {})
        
        data.append({
            "submission_id": attempt.get("id"),
            "student": {"display_name": profile.get("display_name", "Unknown") if profile else "Unknown"},
            "tool_name": "Unknown Tool", # Optional, hard to get without more joins
            "submitted_at": attempt.get("created_at"),
            "ai_total_percentage": item.get("total_score", 0),
            "ai_confidence": 0.0,
            "flags": []
        })
        
    return {"data": data}

@router.get("/submissions/{attempt_id}")
async def get_submission_detail(attempt_id: str):
    """
    讀取單一作答的完整資訊：
    - step_attempts 的 user_input_content
    - 對應的 ai_evaluations（dimension_scores, feedback_text, total_score）
    - prompt_logs（提示使用紀錄）
    """
    db = get_supabase()
    
    # 1. step_attempts & ai_evaluations
    attempt_res = db.table("step_attempts").select("*, ai_evaluations(*)").eq("id", attempt_id).execute()
    if not attempt_res.data:
        raise HTTPException(status_code=404, detail="Submission not found")
        
    attempt_data = attempt_res.data[0]
    eval_data = attempt_data.get("ai_evaluations", [])
    if isinstance(eval_data, list) and len(eval_data) > 0:
        eval_data = eval_data[0]
        
    # 2. prompt_logs
    prompt_res = db.table("prompt_logs").select("*").eq("attempt_id", attempt_id).execute()
    
    return {
        "data": {
            "submission_id": attempt_data.get("id"),
            "content": attempt_data.get("user_input_content"),
            "ai_evaluation": {
                "dimension_scores": eval_data.get("dimension_scores", []) if eval_data else [],
                "feedback_text": eval_data.get("feedback_text", "") if eval_data else "",
                "total_score": eval_data.get("total_score", 0) if eval_data else 0
            },
            "prompt_logs": prompt_res.data
        }
    }

@router.post("/submissions/{attempt_id}/judge")
async def judge_submission(attempt_id: str, body: JudgeRequest):
    """
    寫入 teacher_reviews 資料表，欄位：
    - attempt_id
    - action
    - teacher_comment
    - dimension_overrides（JSONB）
    - require_redo
    - reviewed_at（now()）
    """
    db = get_supabase()
    
    # Validate attempt_id
    attempt_res = db.table("step_attempts").select("id").eq("id", attempt_id).execute()
    if not attempt_res.data:
        raise HTTPException(status_code=404, detail="Submission not found")
        
    # Insert into teacher_reviews
    review_data = {
        "attempt_id": attempt_id,
        "action": body.action,
        "teacher_comment": body.teacher_comment,
        "dimension_overrides": [d.dict() for d in body.dimension_overrides],
        "require_redo": body.require_redo,
        "reviewed_at": datetime.now(timezone.utc).isoformat()
    }
    
    res = db.table("teacher_reviews").insert(review_data).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to save review")
        
    # Optionally update ai_evaluations to set teacher_review_id
    review_id = res.data[0]["id"]
    db.table("ai_evaluations").update({"teacher_review_id": review_id}).eq("attempt_id", attempt_id).execute()
    
    return {"data": res.data[0]}
