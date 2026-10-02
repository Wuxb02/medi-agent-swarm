"""FastAPI 应用入口"""

from pathlib import Path
import sys
from loguru import logger
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.responses import JSONResponse

from mediZJ.api.auth import (
    COOKIE_NAME,
    get_auth_service,
    get_current_user,
    require_admin,
)
from mediZJ.api.routers import (
    auth,
    chat,
    dashboard,
    evolution,
    governance,
    knowledge,
    personal,
    sessions,
    traces,
)
from mediZJ.memory.session_db import SessionDB

import asyncio
from contextlib import asynccontextmanager

from mediZJ.infrastructure.database import (
    close_database,
    initialize_database,
    transaction,
    validate_runtime_schema,
)
from mediZJ.infrastructure.redis_client import (
    close_redis,
    get_redis,
    initialize_redis,
)
from mediZJ.infrastructure.settings import get_settings
from mediZJ.infrastructure.jobs import JobWorker
from mediZJ.infrastructure.handlers import get_handlers

logger.remove()
logger.configure(extra={"run_id": "-", "job_id": "-"})
logger.add(
    sys.stderr,
    level="INFO",
    backtrace=False,
    diagnose=False,
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | "
    "run={extra[run_id]} task={extra[job_id]} | {message}",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@asynccontextmanager
async def lifespan(application):
    settings = get_settings()
    workers = []
    scheduler = None
    scheduler_stop = asyncio.Event()
    try:
        await initialize_database()
        await validate_runtime_schema()
        await initialize_redis()
        uploads = Path(settings.upload_dir)
        uploads.mkdir(parents=True, exist_ok=True)
        from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
        from mediZJ.memory.session_vector_store import SessionVectorStore

        application.state.knowledge = await asyncio.to_thread(MedicalKnowledgeBase)
        application.state.vectors = await asyncio.to_thread(SessionVectorStore)
        handlers = get_handlers()
        workers = [
            JobWorker({"chat": handlers.pop("chat")}, settings.run_concurrency),
            JobWorker(handlers, settings.background_llm_limit),
        ]
        application.state.workers = workers
        for worker in workers:
            await worker.start()

        async def schedule():
            from mediZJ.infrastructure.jobs import enqueue

            while not scheduler_stop.is_set():
                try:
                    from mediZJ.infrastructure.indexing import reconcile

                    await reconcile()
                    async with transaction() as conn:
                        day = (
                            await conn.execute("SELECT UTC_DATE() AS day")
                        ).fetchone()["day"]
                        await enqueue("lifecycle", f"lifecycle:{day}", {})
                    await asyncio.wait_for(scheduler_stop.wait(), 60)
                except asyncio.TimeoutError:
                    pass
                except Exception as exc:
                    logger.error("清理任务调度失败: {}", type(exc).__name__)
                    await asyncio.sleep(1)

        scheduler = asyncio.create_task(schedule())
        yield
    finally:
        scheduler_stop.set()
        if scheduler is not None:
            await scheduler
        await asyncio.gather(*(worker.stop() for worker in workers))
        for name in ("knowledge", "vectors"):
            store = getattr(application.state, name, None)
            if store is not None:
                await asyncio.to_thread(store.milvus_client.close)
        await close_redis()
        await close_database()


app = FastAPI(
    title="MediZJ Agent Swarm API",
    description="多智能体医疗助手系统 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def authenticate_request(request, call_next):
    """解析登录 Cookie，并保护全部业务 API。"""

    if request.url.path in {"/health/live", "/health/ready"}:
        return await call_next(request)
    token = request.cookies.get(COOKIE_NAME)
    request.state.user = await get_auth_service().authenticate(token)

    public_paths = {
        "/",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/health",
        "/health/live",
        "/health/ready",
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
app.include_router(governance.router)


@app.get("/uploads/{filename}")
async def get_uploaded_image(
    filename: str,
    user: dict = Depends(get_current_user),
):
    """读取当前用户上传的图片；旧文件仅管理员可访问。"""

    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=404, detail="Image not found")
    metadata = await SessionDB().get_upload(safe_name)
    if metadata is None:
        raise HTTPException(status_code=404, detail="Image not found")
    else:
        if metadata["user_id"] != user["user_id"] and user["role"] != "admin":
            raise HTTPException(status_code=404, detail="Image not found")
        content_type = metadata["content_type"]
    path = Path(get_settings().upload_dir) / safe_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type=content_type)


@app.get("/")
async def root():
    index = _PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "MediZJ Agent Swarm API", "docs": "/docs"}


@app.get("/health/live")
async def liveness():
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness():
    try:
        async with transaction() as conn:
            await conn.execute("SELECT 1")
        await get_redis().ping()
        await asyncio.to_thread(app.state.knowledge.milvus_client.list_collections)
        if not Path(get_settings().upload_dir).is_dir():
            raise RuntimeError("共享上传目录不可用")
        import os

        if not os.access(get_settings().upload_dir, os.R_OK | os.W_OK | os.X_OK):
            raise RuntimeError("共享上传目录不可读写")
        if any(task.done() for worker in app.state.workers for task in worker.tasks):
            raise RuntimeError("工作器停止")
    except Exception as exc:
        logger.error("就绪检查失败: {}", type(exc).__name__)
        raise HTTPException(status_code=503, detail="基础设施未就绪") from exc
    return {"status": "ready"}


@app.get("/api/metrics")
async def metrics(user: dict = Depends(require_admin)):
    from mediZJ.infrastructure.metrics import snapshot

    return await snapshot()


@app.get("/{path:path}")
async def spa(path: str):
    base = (_PROJECT_ROOT / "frontend" / "dist").resolve()
    asset = (base / path).resolve()
    if not asset.is_relative_to(base) or path.startswith(
        ("api/", "uploads/", "health/")
    ):
        raise HTTPException(404, "Not found")
    if asset.is_file():
        return FileResponse(asset)
    index = base / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(404, "Not found")
