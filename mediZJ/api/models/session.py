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
    parallel_efficiency: float = 0.0
    information_coverage: float = 0.0
    redundancy: float = 0.0


class SessionListResponse(BaseModel):
    """会话列表响应"""
    sessions: List[SessionListItem] = []
    total: int = 0


class SessionTurn(BaseModel):
    """多轮会话中的单轮对话"""
    turn_index: int = 0
    user_message: Dict[str, Any] = {}       # {role, content, timestamp}
    assistant_message: Dict[str, Any] = {}  # {role, content, timestamp, agent_events, ...}


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
    subtasks_completed: int = 0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    parallel_efficiency: float = 0.0
    information_coverage: float = 0.0
    redundancy: float = 0.0
    # 多轮会话支持（旧会话为空列表）
    turns: List[SessionTurn] = []
