"""问答接口的请求/响应模型"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel


class ChatRequest(BaseModel):
    """问答请求"""
    question: str
    session_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    enable_swarm: bool = True


class ChatResponse(BaseModel):
    """问答响应"""
    answer: str
    suggestions: List[str] = []
    disclaimer: str = ""
    session_id: str
    swarm_enabled: bool = False
    agents_involved: List[str] = []
    subtasks_completed: int = 0
    total_time: float = 0.0
    swarm_metadata: Dict[str, Any] = {}
    timeout_occurred: bool = False


class MessageItem(BaseModel):
    """单条消息"""
    role: str
    content: str
    timestamp: Optional[str] = None


class MessageHistory(BaseModel):
    """会话历史"""
    session_id: str
    messages: List[MessageItem] = []
