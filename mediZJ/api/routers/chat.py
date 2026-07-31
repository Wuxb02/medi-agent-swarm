"""问答路由"""
import uuid
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from starlette.responses import StreamingResponse

from mediZJ.api.models.chat import ChatRequest, ChatResponse, MessageHistory, MessageItem, AnswerRequest, AnswerResponse
from mediZJ.api.services.chat_service import chat_non_stream, chat_stream, get_manager

router = APIRouter(prefix="/api/chat", tags=["chat"])

# 图片上传目录
_UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_MAX_SIZE = 10 * 1024 * 1024  # 10MB


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
async def chat(request: ChatRequest):
    """非流式问答"""
    return await chat_non_stream(request)


@router.post("/stream")
async def chat_stream_endpoint(chat_req: ChatRequest, http_request: Request):
    """流式问答（换行分隔 JSON）"""
    return StreamingResponse(
        chat_stream(chat_req, http_request),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@router.post("/answer", response_model=AnswerResponse)
async def submit_answer(request: AnswerRequest):
    """提交问卷答案（用于交互式问诊）"""
    manager = get_manager(request.session_id)
    resolved = manager.resolve(request.questionnaire_id, request.answers)
    if resolved:
        return AnswerResponse(success=True, message="答案已提交")
    else:
        return AnswerResponse(success=False, message="未找到对应问卷或问卷已完成")


@router.get("/history/{session_id}", response_model=MessageHistory)
async def get_chat_history(session_id: str):
    """获取会话历史"""
    from mediZJ.memory.short_term import ShortTermMemory

    memory = ShortTermMemory()
    raw_messages = await memory.get_recent_messages(session_id=session_id, limit=50)

    # 内存无数据时从 SQLite 加载（同步驱动，下线程执行）
    if not raw_messages:
        import asyncio
        from mediZJ.memory.session_db import SessionDB
        db = SessionDB()
        session_data = await asyncio.to_thread(db.get_session, session_id)
        if session_data:
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
            images=msg.get("images"),
            timestamp=msg.get("timestamp")
        )
        for msg in raw_messages
    ]

    return MessageHistory(session_id=session_id, messages=messages)


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
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

    from loguru import logger
    logger.info(f"Image uploaded: {unique_name} ({len(content)} bytes)")

    return {
        "url": url,
        "filename": file.filename,
        "size": len(content),
        "content_type": content_type,
    }
