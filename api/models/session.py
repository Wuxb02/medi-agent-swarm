"""会话管理接口的请求/响应模型"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class SessionListItem(BaseModel):
    """会话列表项"""
    session_id: str
    first_question: str = ""
    created_at: str = ""
    message_count: int = 0
    mode: str = "single"
    total_tokens: int = 0


class SessionListResponse(BaseModel):
    """会话列表响应"""
    sessions: List[SessionListItem] = []
    total: int = 0


class SessionDetail(BaseModel):
    """会话详情"""
    session_id: str
    question: str = ""
    answer: str = ""
    mode: str = "single"
    agents_involved: List[str] = []
    total_time: float = 0.0
    created_at: str = ""
    # 从 JSON 事件文件恢复的完整数据
    agent_events: List[Dict[str, Any]] = []
    suggestions: List[str] = []
    disclaimer: str = ""
    subtasks_completed: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
