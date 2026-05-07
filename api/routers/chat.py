"""问答路由"""
from fastapi import APIRouter
from starlette.responses import StreamingResponse

from api.models.chat import ChatRequest, ChatResponse, MessageHistory, MessageItem
from api.services.chat_service import chat_non_stream, chat_stream

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """非流式问答"""
    return await chat_non_stream(request)


@router.post("/stream")
async def chat_stream_endpoint(request: ChatRequest):
    """流式问答（换行分隔 JSON）"""
    return StreamingResponse(
        chat_stream(request),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@router.get("/history/{session_id}", response_model=MessageHistory)
async def get_chat_history(session_id: str):
    """获取会话历史"""
    from memory.short_term import ShortTermMemory

    memory = ShortTermMemory()
    raw_messages = await memory.get_recent_messages(session_id=session_id, limit=50)

    # 内存无数据时从 SQLite 加载
    if not raw_messages:
        from memory.session_db import SessionDB
        db = SessionDB()
        session_data = db.get_session(session_id)
        if session_data:
            raw_messages = [
                {"role": m["role"], "content": m["content"], "timestamp": m.get("timestamp")}
                for m in session_data.get("messages", [])
                if m.get("role") in ("user", "assistant")
            ]

    messages = [
        MessageItem(
            role=msg.get("role", "unknown"),
            content=msg.get("content", ""),
            timestamp=msg.get("timestamp")
        )
        for msg in raw_messages
    ]

    return MessageHistory(session_id=session_id, messages=messages)
