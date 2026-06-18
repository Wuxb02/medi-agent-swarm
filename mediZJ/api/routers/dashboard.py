"""仪表盘路由"""
import time
from fastapi import APIRouter

from mediZJ.api.models.dashboard import DashboardStats, HealthResponse
from mediZJ.api.services.dashboard_service import get_dashboard_stats

router = APIRouter(prefix="/api", tags=["dashboard"])

# 记录启动时间
_start_time = time.time()


@router.get("/dashboard/stats", response_model=DashboardStats)
async def get_stats():
    """获取仪表盘统计数据"""
    return get_dashboard_stats()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    import os

    # 检查 LLM 配置
    llm_connected = bool(os.getenv("LLM_API_KEY"))

    # 检查知识库
    knowledge_base_ready = False
    try:
        from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
        kb = MedicalKnowledgeBase()
        knowledge_base_ready = kb.collection is not None
    except Exception:
        pass

    # 检查 Mem0
    memory_enabled = bool(os.getenv("MEM0_API_KEY"))

    status = "healthy" if llm_connected else "degraded"

    return HealthResponse(
        status=status,
        version="0.1.0",
        llm_connected=llm_connected,
        knowledge_base_ready=knowledge_base_ready,
        memory_enabled=memory_enabled,
        uptime=time.time() - _start_time,
    )
