"""仪表盘服务：统计数据聚合"""
import os
from typing import Dict, Any
from loguru import logger

from api.services.session_service import list_sessions
from api.services.knowledge_service import get_knowledge_base_size
from api.models.dashboard import DashboardStats
from api.models.session import SessionListItem


def get_dashboard_stats() -> DashboardStats:
    """获取仪表盘统计数据"""
    sessions = list_sessions(limit=200)

    total_sessions = len(sessions)
    swarm_sessions = sum(1 for s in sessions if s.mode == "swarm")
    single_sessions = total_sessions - swarm_sessions

    # Agent 使用统计（从会话文件中推断）
    agents_usage: Dict[str, int] = {
        "consultation_agent": 0,
        "diagnostic_agent": 0,
        "research_agent": 0,
    }
    # 简化统计：单 Agent 模式都算 consultation，swarm 模式算所有
    for s in sessions:
        if s.mode == "swarm":
            agents_usage["consultation_agent"] += 1
            agents_usage["diagnostic_agent"] += 1
            agents_usage["research_agent"] += 1
        else:
            agents_usage["consultation_agent"] += 1

    recent_sessions = sessions[:10]

    return DashboardStats(
        total_sessions=total_sessions,
        total_messages=total_sessions * 2,  # 粗略估计：每会话一问一答
        swarm_sessions=swarm_sessions,
        single_sessions=single_sessions,
        avg_response_time=0.0,  # 需要从详细数据计算
        agents_usage=agents_usage,
        knowledge_base_size=get_knowledge_base_size(),
        recent_sessions=recent_sessions,
    )
