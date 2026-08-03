"""会话管理路由"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException

from mediZJ.api.models.session import SessionListResponse, SessionDetail
from mediZJ.api.services.session_service import list_sessions, count_sessions, get_session_detail, delete_session
from mediZJ.api.auth import get_current_user

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
async def get_sessions(
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    """获取会话列表（SQLite 同步驱动，下线程执行）"""
    sessions, total = await asyncio.gather(
        asyncio.to_thread(list_sessions, limit, offset, user["user_id"]),
        asyncio.to_thread(count_sessions, user["user_id"]),
    )
    return SessionListResponse(sessions=sessions, total=total)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """获取会话详情"""
    detail = await asyncio.to_thread(
        get_session_detail,
        session_id,
        user["user_id"],
    )
    if not detail:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.delete("/{session_id}")
async def remove_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """删除会话"""
    success = await asyncio.to_thread(
        delete_session,
        session_id,
        user["user_id"],
    )
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}
