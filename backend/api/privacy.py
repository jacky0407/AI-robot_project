from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.auth import CurrentUser, get_current_user
from core.privacy import detect_pii

router = APIRouter(prefix="/api/privacy", tags=["privacy"])

class PIICheckRequest(BaseModel):
    content: str = Field(max_length=20000)

@router.post("/detect")
async def detect_pii_in_content(body: PIICheckRequest, user: CurrentUser = Depends(get_current_user)):
    """作答前的個資檢查。需要登入：這支 API 沒有任何頁面在未登入時使用"""
    result = detect_pii(body.content)
    return {
        "data": {
            "has_pii_risk": result.has_risk,
            "detected_types": result.detected_types,
            "warning": result.warning_message
        }
    }
