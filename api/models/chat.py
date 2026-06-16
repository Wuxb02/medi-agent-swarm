"""问答接口的请求/响应模型"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel


class Citation(BaseModel):
    """知识库引用"""
    index: int
    doc_id: str = ""
    source: str = ""
    disease: str = ""
    type: str = ""
    filename: str = ""
    score: float = 0.0
    snippet: str = ""
    content: str = ""


class ChatRequest(BaseModel):
    """问答请求"""
    question: str
    session_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


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
    usage: Dict[str, int] = {}  # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
    citations: List[Citation] = []


class MessageItem(BaseModel):
    """单条消息"""
    role: str
    content: str
    timestamp: Optional[str] = None


class MessageHistory(BaseModel):
    """会话历史"""
    session_id: str
    messages: List[MessageItem] = []


class AnswerRequest(BaseModel):
    """问卷答案提交请求"""
    questionnaire_id: str
    answers: Dict[str, Any]
    session_id: str


class AnswerResponse(BaseModel):
    """问卷答案提交响应"""
    success: bool
    message: str = ""
