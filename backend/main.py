import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from api.practice import router as practice_router
from api import teacher

settings = get_settings()

app = FastAPI(
    title="EduBloom_AI API（早療特教專業發展實驗室）",
    description="學前特殊教育與早期療育專業能力培訓平台後端 API",
    version="1.0.0",
)

# CORS 設定：只允許 FRONTEND_URL 列出的前端網址（可用逗號分隔多個）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,  # 必須為 True 才能接受 Cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

# 掛載路由
from api.privacy import router as privacy_router
from api.review import router as review_router
from api.courses import router as courses_router
from api.tool_requests import router as tool_requests_router
from api.course_modules import router as course_modules_router
from api.modules import router as modules_router
from api.admin import router as admin_router
from api.me import router as me_router
from api.export import router as export_router
from api.chat import router as chat_router
from api.materials import router as materials_router
from api.templates import router as templates_router
from api.audit_logs import router as audit_logs_router

app.include_router(practice_router)
# TODO: 等成員二實作後，在此加入其他 router
# app.include_router(auth_router)
# app.include_router(tools_router)
app.include_router(courses_router)
app.include_router(tool_requests_router)
app.include_router(course_modules_router)
app.include_router(modules_router)
app.include_router(privacy_router)
app.include_router(review_router)
app.include_router(teacher.router)
app.include_router(admin_router)
app.include_router(me_router)
app.include_router(export_router)
app.include_router(chat_router)
app.include_router(materials_router)
app.include_router(templates_router)
app.include_router(audit_logs_router)


@app.get("/")
def read_root():
    return {"message": "EduBloom_AI API（早療特教專業發展實驗室）", "version": "1.0.0"}


@app.get("/api/health")
def health_check():
    # 部署除錯用：看得出雲端跑的是哪個分支／commit、CORS 實際允許哪些前端網址。
    # 前端網址本來就是公開的，不含任何秘密；RENDER_GIT_* 由 Render 自動提供，本機為 null。
    return {
        "status": "ok",
        "environment": settings.environment,
        "branch": os.environ.get("RENDER_GIT_BRANCH"),
        "commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7] or None,
        "cors_origins": settings.cors_origins,
    }
