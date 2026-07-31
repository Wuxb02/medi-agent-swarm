"""FastAPI 应用入口"""
from pathlib import Path
from loguru import logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 文件日志配置（始终相对于项目根目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
log_dir = _PROJECT_ROOT / "logs"
log_dir.mkdir(exist_ok=True)
logger.add(
    log_dir / "app_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
)

from mediZJ.api.routers import chat, knowledge, sessions, dashboard, personal, traces

app = FastAPI(
    title="MediZJ Agent Swarm API",
    description="多智能体医疗助手系统 API",
    version="0.1.0",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(sessions.router)
app.include_router(dashboard.router)
app.include_router(personal.router)
app.include_router(traces.router)

# 挂载上传目录为静态文件服务（图片访问）
_uploads_path = _PROJECT_ROOT / "mediZJ" / "data" / "uploads"
_uploads_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_path)), name="uploads")


@app.get("/")
async def root():
    return {"message": "MediZJ Agent Swarm API", "docs": "/docs"}
