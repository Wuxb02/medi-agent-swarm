"""仪表盘服务：统计数据聚合"""
from typing import Dict, List, Optional
from mediZJ.api.services.session_service import list_sessions, get_session_detail
from mediZJ.api.services.knowledge_service import get_knowledge_base_size
from mediZJ.api.models.dashboard import DashboardStats


def get_dashboard_stats(user_id: Optional[str] = None) -> DashboardStats:
    """获取仪表盘统计数据"""
    sessions = list_sessions(limit=200, user_id=user_id)

    total_sessions = len(sessions)
    swarm_sessions = sum(1 for s in sessions if s.mode == "swarm")
    total_tokens = sum(s.total_tokens for s in sessions)
    total_messages = sum(s.message_count for s in sessions)
    single_sessions = total_sessions - swarm_sessions

    # Agent 使用统计：从会话详情中获取真实的 agents_involved 数据
    agents_usage: Dict[str, int] = {}
    response_times: List[float] = []

    for s in sessions:
        detail = get_session_detail(s.session_id, user_id=user_id)
        if detail:
            # 按实际参与的 Agent 统计
            for agent in detail.agents_involved:
                if agent:  # 过滤空字符串
                    agents_usage[agent] = agents_usage.get(agent, 0) + 1
            # 收集响应时间
            if detail.total_time > 0:
                response_times.append(detail.total_time)
        else:
            # 降级：无法获取详情时按模式推断
            if s.mode == "swarm":
                for agent in ["consultation_agent", "diagnostic_agent", "research_agent"]:
                    agents_usage[agent] = agents_usage.get(agent, 0) + 1
            else:
                agents_usage["consultation_agent"] = agents_usage.get("consultation_agent", 0) + 1

    avg_response_time = (
        sum(response_times) / len(response_times) if response_times else 0.0
    )

    # 计算性能指标平均值（仅 swarm 会话）
    swarm_metrics = [
        (s.parallel_efficiency, s.information_coverage, s.redundancy)
        for s in sessions
        if s.mode == "swarm" and s.parallel_efficiency > 0
    ]
    if swarm_metrics:
        avg_pe = sum(m[0] for m in swarm_metrics) / len(swarm_metrics)
        avg_ic = sum(m[1] for m in swarm_metrics) / len(swarm_metrics)
        avg_rd = sum(m[2] for m in swarm_metrics) / len(swarm_metrics)
    else:
        avg_pe = avg_ic = avg_rd = 0.0

    recent_sessions = sessions[:10]

    return DashboardStats(
        total_sessions=total_sessions,
        total_messages=total_messages,
        swarm_sessions=swarm_sessions,
        single_sessions=single_sessions,
        avg_response_time=round(avg_response_time, 2),
        agents_usage=agents_usage,
        knowledge_base_size=get_knowledge_base_size(),
        recent_sessions=recent_sessions,
        total_tokens=total_tokens,
        avg_parallel_efficiency=round(avg_pe, 4),
        avg_information_coverage=round(avg_ic, 4),
        avg_redundancy=round(avg_rd, 4),
    )
