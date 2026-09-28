from fastapi import APIRouter
from pydantic import BaseModel
from core.privacy import detect_pii

router = APIRouter(prefix="/api/privacy", tags=["privacy"])

class PIICheckRequest(BaseModel):
    content: str

@router.post("/detect")
async def detect_pii_in_content(body: PIICheckRequest):
    result = detect_pii(body.content)
    return {
        "data": {
            "has_pii_risk": result.has_risk,
            "detected_types": result.detected_types,
            "warning": result.warning_message
        }
    }
