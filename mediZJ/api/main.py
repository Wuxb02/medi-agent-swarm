"""FastAPI 应用入口"""
from pathlib import Path
from loguru import logger
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse

from mediZJ.api.auth import COOKIE_NAME, get_auth_service, get_current_user
from mediZJ.api.routers import (
    auth,
    chat,
    dashboard,
    evolution,
    knowledge,
    personal,
    sessions,
    traces,
)
from mediZJ.memory.session_db import SessionDB

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


@app.middleware("http")
async def authenticate_request(request, call_next):
    """解析登录 Cookie，并保护全部业务 API。"""

    token = request.cookies.get(COOKIE_NAME)
    request.state.user = get_auth_service().authenticate(token)

    public_paths = {
        "/",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
    is_public = request.url.path in public_paths
    if (
        request.method != "OPTIONS"
        and (
            request.url.path.startswith("/api/")
            or request.url.path.startswith("/uploads/")
        )
        and not is_public
        and request.state.user is None
    ):
        return JSONResponse(status_code=401, content={"detail": "请先登录"})
    return await call_next(request)

# 注册路由
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(sessions.router)
app.include_router(dashboard.router)
app.include_router(personal.router)
app.include_router(traces.router)
app.include_router(evolution.router)


@app.on_event("startup")
async def start_evolution_worker():
    """启动不阻塞主对话的自进化评审工作器。"""
    from mediZJ.evolution import EvolutionService

    await EvolutionService().start()


@app.on_event("shutdown")
async def stop_evolution_worker():
    """平滑停止自进化评审工作器。"""
    from mediZJ.evolution import EvolutionService

    await EvolutionService().stop()

# 上传目录通过鉴权路由提供，避免患者图片公开暴露
_uploads_path = _PROJECT_ROOT / "mediZJ" / "data" / "uploads"
_uploads_path.mkdir(parents=True, exist_ok=True)


@app.get("/uploads/{filename}")
async def get_uploaded_image(
    filename: str,
    user: dict = Depends(get_current_user),
):
    """读取当前用户上传的图片；旧文件仅管理员可访问。"""

    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=404, detail="Image not found")
    metadata = SessionDB().get_upload(safe_name)
    if metadata is None:
        if user["role"] != "admin":
            raise HTTPException(status_code=404, detail="Image not found")
        content_type = None
    else:
        if metadata["user_id"] != user["user_id"] and user["role"] != "admin":
            raise HTTPException(status_code=404, detail="Image not found")
        content_type = metadata["content_type"]
    path = _uploads_path / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type=content_type)


@app.get("/")
async def root():
    return {"message": "MediZJ Agent Swarm API", "docs": "/docs"}
