"""问答路由"""

import asyncio
import uuid
import json
import os
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from starlette.responses import StreamingResponse

from mediZJ.api.models.chat import (
    ChatRequest,
    ChatResponse,
    MessageHistory,
    MessageItem,
    AnswerRequest,
    AnswerResponse,
)
from mediZJ.api.services.run_service import (
    create_run,
    get_run,
    answer_run,
    cancel_run,
    read_events,
)
from mediZJ.infrastructure.settings import get_settings
from mediZJ.api.auth import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 图片上传目录
_UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MAX_SIZE = 10 * 1024 * 1024  # 10MB


async def _validate_owned_images(images: list[str] | None, user: dict) -> None:
    """确保聊天引用的每张图片都属于当前用户。"""

    if not images:
        return
    from mediZJ.memory.session_db import SessionDB

    db = SessionDB()
    for image_url in images:
        filename = Path(image_url).name
        metadata = await db.get_upload(filename)
        if metadata is None:
            if user["role"] == "admin" and (_UPLOAD_DIR / filename).is_file():
                continue
            raise HTTPException(status_code=404, detail="Image not found")
        if metadata["user_id"] != user["user_id"] and user["role"] != "admin":
            raise HTTPException(status_code=404, detail="Image not found")


def _detect_image_type(data: bytes) -> str | None:
    """通过文件头魔数检测图片类型（替代 Python 3.13 中已移除的 imghdr）"""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and len(data) > 11 and data[8:12] == b"WEBP":
        return "webp"
    return None


_subscribers = 0


class EventResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        global _subscribers
        try:
            await super().__call__(scope, receive, send)
        finally:
            _subscribers -= 1

    async def stream_response(self, send):
        async def bounded_send(message):
            await asyncio.wait_for(send(message), get_settings().slow_client_timeout)

        await super().stream_response(bounded_send)


def reserve_subscription():
    global _subscribers
    if _subscribers >= get_settings().event_subscriber_limit:
        raise HTTPException(503, "事件连接已达上限", headers={"Retry-After": "5"})
    _subscribers += 1


def subscribe(run_id, user_id, after=0, reserved=False):
    if not reserved:
        reserve_subscription()

    async def stream():
        cursor = after
        while True:
            events = await read_events(run_id, user_id, cursor)
            for event in events:
                cursor = event["seq"]
                yield (
                    json.dumps(
                        {
                            "run_id": run_id,
                            "seq": cursor,
                            "event": event["event"],
                            "data": event["data"],
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
            run = await get_run(run_id, user_id)
            if (
                run["status"] in {"completed", "failed", "cancelled", "expired"}
                and not events
            ):
                return
            await asyncio.sleep(0.25)

    return EventResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs", status_code=202)
async def start_run(
    request: ChatRequest, http_request: Request, user: dict = Depends(get_current_user)
):
    await _validate_owned_images(request.images, user)
    run = await create_run(
        request, user["user_id"], http_request.headers.get("Idempotency-Key")
    )
    return {
        "run_id": run["run_id"],
        "session_id": run["session_id"],
        "status": run["status"],
    }


@router.get("/runs/{run_id}")
async def run_status(run_id: str, user: dict = Depends(get_current_user)):
    return await get_run(run_id, user["user_id"])


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str, after: int = 0, user: dict = Depends(get_current_user)
):
    if after < 0:
        raise HTTPException(422, "事件序号不能为负数")
    await get_run(run_id, user["user_id"])
    return subscribe(run_id, user["user_id"], after)


@router.post("/runs/{run_id}/cancel")
async def stop_run(run_id: str, user: dict = Depends(get_current_user)):
    return await cancel_run(run_id, user["user_id"])


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest, http_request: Request, user: dict = Depends(get_current_user)
):
    await _validate_owned_images(request.images, user)
    run = await create_run(
        request,
        user["user_id"],
        http_request.headers.get("Idempotency-Key"),
        hitl=False,
    )
    while run["status"] not in {"completed", "failed", "cancelled", "expired"}:
        await asyncio.sleep(0.25)
        run = await get_run(run["run_id"], user["user_id"])
    if run["status"] != "completed":
        raise HTTPException(503, run.get("error") or run["status"])
    return ChatResponse(**run["result"])


@router.post("/stream")
async def chat_stream_endpoint(
    request: ChatRequest, http_request: Request, user: dict = Depends(get_current_user)
):
    await _validate_owned_images(request.images, user)
    global _subscribers
    reserve_subscription()
    try:
        run = await create_run(
            request, user["user_id"], http_request.headers.get("Idempotency-Key")
        )
        return subscribe(run["run_id"], user["user_id"], reserved=True)
    except BaseException:
        _subscribers -= 1
        raise


@router.post("/answer", response_model=AnswerResponse)
async def submit_answer(request: AnswerRequest, user: dict = Depends(get_current_user)):
    await answer_run(
        request.run_id,
        request.session_id,
        request.questionnaire_id,
        request.answers,
        user["user_id"],
    )
    return AnswerResponse(success=True, message="答案已持久化")


@router.get("/history/{session_id}", response_model=MessageHistory)
async def get_chat_history(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """获取会话历史"""
    from mediZJ.memory.short_term import ShortTermMemory
    from mediZJ.memory.session_db import SessionDB

    db = SessionDB()
    session_data = await db.get_session(session_id, user["user_id"])
    if session_data is None:
        raise HTTPException(status_code=404, detail="Session not found")

    memory = ShortTermMemory(user_id=user["user_id"])
    raw_messages = await memory.get_recent_messages(session_id=session_id, limit=50)

    # 内存无数据时从 SQLite 加载（同步驱动，下线程执行）
    if not raw_messages:
        raw_messages = [
            {
                "role": m["role"],
                "content": m["content"],
                "timestamp": m.get("timestamp"),
                "images": m.get("images"),
            }
            for m in session_data.get("messages", [])
            if m.get("role") in ("user", "assistant")
        ]

    messages = [
        MessageItem(
            role=msg.get("role", "unknown"),
            content=msg.get("content", ""),
            images=(msg.get("images") if isinstance(msg.get("images"), list) else None),
            timestamp=msg.get("timestamp"),
        )
        for msg in raw_messages
    ]

    return MessageHistory(session_id=session_id, messages=messages)


@router.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """上传聊天图片（用于多模态分析）"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的图片格式：{ext}，支持：{', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    chunks = []
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > _MAX_SIZE:
            raise HTTPException(status_code=413, detail="图片最大 10MB")
        chunks.append(chunk)
    content = b"".join(chunks)
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    # 通过文件头魔数检测图片类型（imghdr 在 Python 3.13 中已移除）
    detected_type = _detect_image_type(content)
    if detected_type is None:
        raise HTTPException(
            status_code=400,
            detail="无法识别图片格式，请上传有效的 JPEG/PNG/GIF/WebP 图片",
        )

    unique_name = f"{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:12]}{ext}"
    save_path = _UPLOAD_DIR / unique_name
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temporary = _UPLOAD_DIR / f".{unique_name}.pending"
    await asyncio.to_thread(temporary.write_bytes, content)
    await asyncio.to_thread(temporary.replace, save_path)

    url = f"/uploads/{unique_name}"
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    content_type = mime_map.get(ext, "image/jpeg")

    from mediZJ.memory.session_db import SessionDB

    (
        await SessionDB().save_upload(
            filename=unique_name,
            user_id=user["user_id"],
            original_name=file.filename,
            content_type=content_type,
            size=len(content),
        )
    )

    from loguru import logger

    logger.info(f"Image uploaded: {unique_name} ({len(content)} bytes)")

    return {
        "url": url,
        "filename": file.filename,
        "size": len(content),
        "content_type": content_type,
    }
