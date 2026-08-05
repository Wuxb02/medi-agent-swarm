"""问答路由"""
import asyncio
import uuid
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from starlette.responses import StreamingResponse

from mediZJ.api.models.chat import ChatRequest, ChatResponse, MessageHistory, MessageItem, AnswerRequest, AnswerResponse
from mediZJ.api.services.chat_service import (
    chat_non_stream,
    chat_stream,
    claim_session,
    get_manager,
    session_owner,
)
from mediZJ.api.auth import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 图片上传目录
_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MAX_SIZE = 10 * 1024 * 1024  # 10MB


def _validate_owned_images(images: list[str] | None, user: dict) -> None:
    """确保聊天引用的每张图片都属于当前用户。"""

    if not images:
        return
    from mediZJ.memory.session_db import SessionDB

    db = SessionDB()
    for image_url in images:
        filename = Path(image_url).name
        metadata = db.get_upload(filename)
        if metadata is None:
            if user["role"] == "admin" and (_UPLOAD_DIR / filename).is_file():
                continue
            raise HTTPException(status_code=404, detail="Image not found")
        if metadata["user_id"] != user["user_id"] and user["role"] != "admin":
            raise HTTPException(status_code=404, detail="Image not found")


def _detect_image_type(data: bytes) -> str | None:
    """通过文件头魔数检测图片类型（替代 Python 3.13 中已移除的 imghdr）"""
    if data[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:4] == b'RIFF' and len(data) > 11 and data[8:12] == b'WEBP':
        return 'webp'
    return None


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: dict = Depends(get_current_user),
):
    """非流式问答"""
    authenticated_request = request.model_copy(
        update={"user_id": user["user_id"]}
    )
    _validate_owned_images(authenticated_request.images, user)
    if authenticated_request.session_id:
        try:
            claim_session(authenticated_request.session_id, user["user_id"])
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
    return await chat_non_stream(authenticated_request)


@router.post("/stream")
async def chat_stream_endpoint(
    chat_req: ChatRequest,
    http_request: Request,
    user: dict = Depends(get_current_user),
):
    """流式问答（换行分隔 JSON）"""
    authenticated_request = chat_req.model_copy(
        update={"user_id": user["user_id"]}
    )
    _validate_owned_images(authenticated_request.images, user)
    if authenticated_request.session_id:
        try:
            claim_session(authenticated_request.session_id, user["user_id"])
        except PermissionError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
    return StreamingResponse(
        chat_stream(
            authenticated_request,
            http_request,
        ),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@router.post("/answer", response_model=AnswerResponse)
async def submit_answer(
    request: AnswerRequest,
    user: dict = Depends(get_current_user),
):
    """提交问卷答案（用于交互式问诊）

    答案经会话级信号队列传递给正在 interrupt 挂起的 SSE 流，
    由 SSE 内部用 Command(resume=...) 恢复图执行。
    同时保留 QuestionnaireManager.resolve 用于幂等校验（未命中时降级）。
    """
    if session_owner(request.session_id) != user["user_id"]:
        raise HTTPException(status_code=404, detail="Session not found")

    from mediZJ.api.services.session_runtime import put_answer

    if put_answer(request.session_id, request.answers):
        return AnswerResponse(success=True, message="答案已提交")

    # 无活动信号队列（非流式/已清理）：回退到 QuestionnaireManager 兼容逻辑
    manager = get_manager(request.session_id)
    resolved = manager.resolve(request.questionnaire_id, request.answers)
    if resolved:
        return AnswerResponse(success=True, message="答案已提交")
    else:
        return AnswerResponse(success=False, message="未找到对应问卷或问卷已完成")


@router.get("/history/{session_id}", response_model=MessageHistory)
async def get_chat_history(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """获取会话历史"""
    from mediZJ.memory.short_term import ShortTermMemory
    from mediZJ.memory.session_db import SessionDB

    db = SessionDB()
    session_data = await asyncio.to_thread(
        db.get_session,
        session_id,
        user["user_id"],
    )
    if session_data is None:
        raise HTTPException(status_code=404, detail="Session not found")

    memory = ShortTermMemory()
    raw_messages = await memory.get_recent_messages(session_id=session_id, limit=50)

    # 内存无数据时从 SQLite 加载（同步驱动，下线程执行）
    if not raw_messages:
        raw_messages = [
            {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp"),
             "images": m.get("images")}
            for m in session_data.get("messages", [])
            if m.get("role") in ("user", "assistant")
        ]

    messages = [
        MessageItem(
            role=msg.get("role", "unknown"),
            content=msg.get("content", ""),
            images=(
                msg.get("images")
                if isinstance(msg.get("images"), list)
                else None
            ),
            timestamp=msg.get("timestamp")
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
        raise HTTPException(status_code=400, detail=f"不支持的图片格式：{ext}，支持：{', '.join(sorted(_ALLOWED_EXTENSIONS))}")

    content = await file.read()
    if len(content) > _MAX_SIZE:
        raise HTTPException(status_code=400, detail=f"图片过大（{len(content) / 1024 / 1024:.1f}MB），最大 10MB")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    # 通过文件头魔数检测图片类型（imghdr 在 Python 3.13 中已移除）
    detected_type = _detect_image_type(content)
    if detected_type is None:
        raise HTTPException(status_code=400, detail="无法识别图片格式，请上传有效的 JPEG/PNG/GIF/WebP 图片")

    unique_name = f"{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:12]}{ext}"
    save_path = _UPLOAD_DIR / unique_name
    save_path.write_bytes(content)

    url = f"/uploads/{unique_name}"
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".webp": "image/webp"}
    content_type = mime_map.get(ext, "image/jpeg")

    from mediZJ.memory.session_db import SessionDB
    SessionDB().save_upload(
        filename=unique_name,
        user_id=user["user_id"],
        original_name=file.filename,
        content_type=content_type,
        size=len(content),
    )

    from loguru import logger
    logger.info(f"Image uploaded: {unique_name} ({len(content)} bytes)")

    return {
        "url": url,
        "filename": file.filename,
        "size": len(content),
        "content_type": content_type,
    }
