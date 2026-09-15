# database/practice_repo.py
import os
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY") # 使用後端 Service Role Key

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def get_tool_and_case_by_ids(tool_id: str, case_id: str):
    """取得 AI 工具設定與案例內容"""
    tool_res = supabase.table("ai_tools").select("*").eq("id", tool_id).single().execute()
    case_res = supabase.table("tool_cases").select("*").eq("id", case_id).single().execute()
    return tool_res.data, case_res.data

def save_student_attempt_and_evaluation(
    user_id: str,
    module_id: str,
    step_id: str,
    case_id: str,
    content: str,
    eval_data: dict
):
    """將學生的作答嘗試與 AI 評分結果寫入資料庫"""
    # 1. 取得當前是第幾次嘗試
    existing = supabase.table("step_attempts")\
        .select("id", count="exact")\
        .eq("user_id", user_id)\
        .eq("step_id", step_id)\
        .execute()
    attempt_number = (existing.count or 0) + 1

    # 2. 寫入 step_attempts
    attempt_res = supabase.table("step_attempts").insert({
        "user_id": user_id,
        "module_id": module_id,
        "step_id": step_id,
        "case_id": case_id,
        "attempt_number": attempt_number,
        "user_input_content": content,
        "status": "passed" if eval_data.get("passed") else "revision_required"
    }).execute()
    
    attempt_id = attempt_res.data[0]["id"]

    # 3. 寫入 ai_evaluations
    supabase.table("ai_evaluations").insert({
        "attempt_id": attempt_id,
        "total_score": eval_data.get("score"),
        "dimension_scores": eval_data.get("dimensions", {}),
        "evidence_text": eval_data.get("evidence", ""),
        "detected_errors": eval_data.get("errors", []),
        "feedback_text": eval_data.get("feedback", ""),
        "suggested_next_step": eval_data.get("next_step", ""),
        "ai_cost": eval_data.get("cost", 0.0)
    }).execute()

    return attempt_id