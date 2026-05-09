"""问答路由"""
from fastapi import APIRouter
from starlette.responses import StreamingResponse

from api.models.chat import ChatRequest, ChatResponse, MessageHistory, MessageItem, AnswerRequest, AnswerResponse
from api.services.chat_service import chat_non_stream, chat_stream, get_manager

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
