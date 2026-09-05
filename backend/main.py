from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from api.practice import router as practice_router
from api import teacher

settings = get_settings()

app = FastAPI(
    title="AI 專業能力培訓平台 API",
    description="學前特殊教育與早期療育專業能力培訓平台後端 API",
    version="1.0.0",
)

# CORS 設定：只允許前端網址存取
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,  # 必須為 True 才能接受 Cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

# 掛載路由
app.include_router(practice_router)
# TODO: 等成員二實作後，在此加入其他 router
# app.include_router(auth_router)
# app.include_router(courses_router)
# app.include_router(tools_router)
app.include_router(teacher.router)


@app.get("/")
def read_root():
    return {"message": "AI 專業能力培訓平台 API", "version": "1.0.0"}


@app.get("/api/health")
def health_check():
    return {"status": "ok", "environment": settings.environment}
