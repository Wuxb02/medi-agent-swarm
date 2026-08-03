"""Trace 查询 API 路由"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from mediZJ.trace.analysis import TraceAnalyzer
from mediZJ.trace.storage import TraceSqliteStorage
from mediZJ.api.models.trace import TraceListResponse, WaterfallResponse
from mediZJ.api.auth import get_current_user, require_admin

router = APIRouter(prefix="/api", tags=["traces"])

_storage = TraceSqliteStorage()
_analyzer = TraceAnalyzer()


# ---- 聚合统计路由（必须在 /{trace_id} 之前定义） ----

@router.get("/traces/stats/agents")
async def get_agent_stats(
    days: int = Query(default=7, ge=1, le=90),
    _admin: dict = Depends(require_admin),
):
    """per-agent 统计"""
    return {"period_days": days, "stats": _analyzer.get_agent_stats(days)}


@router.get("/traces/stats/tools")
async def get_tool_stats(
    days: int = Query(default=7, ge=1, le=90),
    _admin: dict = Depends(require_admin),
):
    """per-tool 统计"""
    return {"period_days": days, "stats": _analyzer.get_tool_stats(days)}


@router.get("/traces/stats/llm")
async def get_llm_stats(
    days: int = Query(default=7, ge=1, le=90),
    _admin: dict = Depends(require_admin),
):
    """LLM 调用统计"""
    return {"period_days": days, "stats": _analyzer.get_llm_stats(days)}


@router.get("/traces/stats/slow")
async def get_slow_traces(
    threshold_ms: float = Query(default=30000, ge=1000),
    limit: int = Query(default=10, ge=1, le=100),
    _admin: dict = Depends(require_admin),
):
    """慢 trace 查询"""
    return {"threshold_ms": threshold_ms, "traces": _analyzer.get_slow_traces(threshold_ms, limit)}


@router.get("/traces/stats/errors")
async def get_error_traces(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
    _admin: dict = Depends(require_admin),
):
    """错误 trace 查询"""
    return {"period_days": days, "traces": _analyzer.get_error_traces(days, limit)}


# ---- 列表路由 ----

@router.get("/traces", response_model=TraceListResponse)
async def list_traces(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """最近 trace 列表"""
    user_id = None if user["role"] == "admin" else user["user_id"]
    traces = _storage.list_traces(
        limit=limit,
        offset=offset,
        session_id=session_id,
        user_id=user_id,
    )
    total = _storage.count_traces(session_id=session_id, user_id=user_id)
    return {"traces": traces, "total": total, "limit": limit, "offset": offset}


# ---- 单个 trace 路由 ----

@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    user: dict = Depends(get_current_user),
):
    """完整 trace 树（嵌套 span）"""
    user_id = None if user["role"] == "admin" else user["user_id"]
    tree = _storage.get_trace(trace_id, user_id=user_id)
    if tree is None:
        return {"error": "Trace not found", "trace_id": trace_id}
    return tree


@router.get("/traces/{trace_id}/spans")
async def get_trace_spans(
    trace_id: str,
    user: dict = Depends(get_current_user),
):
    """扁平 span 列表"""
    user_id = None if user["role"] == "admin" else user["user_id"]
    spans = _storage.get_flat_spans(trace_id, user_id=user_id)
    return {"trace_id": trace_id, "spans": spans, "count": len(spans)}


@router.get("/traces/{trace_id}/waterfall", response_model=WaterfallResponse)
async def get_trace_waterfall(
    trace_id: str,
    user: dict = Depends(get_current_user),
):
    """Waterfall 视图数据（含 offset 和 depth）"""
    _ensure_trace_access(trace_id, user)
    return _analyzer.get_waterfall(trace_id)


@router.get("/traces/{trace_id}/stages")
async def get_trace_stages(
    trace_id: str,
    user: dict = Depends(get_current_user),
):
    """阶段耗时分布"""
    _ensure_trace_access(trace_id, user)
    return {
        "trace_id": trace_id,
        "stages": _analyzer.get_stage_breakdown(trace_id),
    }


def _ensure_trace_access(trace_id: str, user: dict) -> None:
    """校验当前用户能否访问指定 Trace。"""

    user_id = None if user["role"] == "admin" else user["user_id"]
    if _storage.get_trace(trace_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Trace not found")
