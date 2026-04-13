"""
FastAPI 应用入口
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os

from app.config import get_settings
from app.database import init_db
from app.routers import auth, issues, chat, users, webhooks, admin
from app.routers import report
from app.routers.issues import startup_sync

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库表
    init_db()
    # 从 JIRA 全量同步一次（非阻塞，失败不影响启动）
    await startup_sync()
    yield


app = FastAPI(
    title="AutoCharge Cowork API",
    description="ACMS 项目 AI 协作平台后端",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 开发环境允许所有来源，生产环境按实际域名限制
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_env == "development" else [
        "https://acms.autocharge.internal"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router)
app.include_router(issues.router)
app.include_router(chat.router)
app.include_router(users.router)
app.include_router(webhooks.router)
app.include_router(admin.router)
app.include_router(report.router)


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.app_env}


# 前端静态文件（放在 API 路由之后，避免冲突）
_FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")

@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join(_FRONTEND, "index.html"))
