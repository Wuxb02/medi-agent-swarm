"""会话管理路由"""
from fastapi import APIRouter, HTTPException

from api.models.session import SessionListResponse, SessionDetail
from api.services.session_service import list_sessions, get_session_detail, delete_session

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
async def get_sessions(limit: int = 50):
    """获取会话列表"""
    sessions = list_sessions(limit=limit)
    return SessionListResponse(sessions=sessions, total=len(sessions))


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str):
    """获取会话详情"""
    detail = get_session_detail(session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.delete("/{session_id}")
async def remove_session(session_id: str):
    """删除会话"""
    success = delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}
