"""仪表盘接口的响应模型"""
from typing import Dict, Any, List
from pydantic import BaseModel
from .session import SessionListItem


class DashboardStats(BaseModel):
    """仪表盘统计数据"""
    total_sessions: int = 0
    total_messages: int = 0
    swarm_sessions: int = 0
    single_sessions: int = 0
    avg_response_time: float = 0.0
    agents_usage: Dict[str, int] = {}
    knowledge_base_size: int = 0
    recent_sessions: List[SessionListItem] = []
    total_tokens: int = 0
    avg_parallel_efficiency: float = 0.0
    avg_information_coverage: float = 0.0
    avg_redundancy: float = 0.0


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "healthy"
    version: str = "0.1.0"
    llm_connected: bool = True
    knowledge_base_ready: bool = True
    memory_enabled: bool = False
    uptime: float = 0.0
