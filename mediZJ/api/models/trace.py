"""Trace API 的 Pydantic 响应模型"""
from typing import Dict, Any, Optional, List
from pydantic import BaseModel


class SpanItem(BaseModel):
    """扁平 span 条目"""
    id: str
    trace_id: str
    parent_id: Optional[str] = None
    span_type: str
    name: str = ""
    status: str = "ok"
    start_time: str = ""
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    llm_attrs: Optional[Dict[str, Any]] = None
    tool_attrs: Optional[Dict[str, Any]] = None
    agent_attrs: Optional[Dict[str, Any]] = None


class TraceSummary(BaseModel):
    """trace 列表汇总条目"""
    trace_id: str
    session_id: str = ""
    status: str = "ok"
    start_time: str = ""
    duration_ms: Optional[float] = None
    mode: str = ""
    total_tokens: int = 0
    agents_involved: List[str] = []
    span_count: int = 0
    question_summary: str = ""


class TraceListResponse(BaseModel):
    """trace 列表响应"""
    traces: List[TraceSummary] = []
    total: int = 0
    limit: int = 50
    offset: int = 0


class WaterfallSpan(BaseModel):
    """Waterfall 视图的 span 条目（含 offset 和 depth）"""
    id: str
    parent_id: Optional[str] = None
    span_type: str
    name: str
    status: str = "ok"
    start_offset_ms: float = 0
    duration_ms: float = 0
    depth: int = 0
    error_message: Optional[str] = None
    attributes: Dict[str, Any] = {}


class WaterfallResponse(BaseModel):
    """Waterfall 视图响应"""
    trace_id: str
    total_duration_ms: float = 0
    spans: List[WaterfallSpan] = []


class AgentStat(BaseModel):
    """per-agent 统计"""
    call_count: int = 0
    avg_duration_ms: float = 0
    p50_ms: float = 0
    p90_ms: float = 0
    success_rate: float = 0
    avg_tokens: int = 0


class ToolStat(BaseModel):
    """per-tool 统计"""
    call_count: int = 0
    avg_duration_ms: float = 0
    success_rate: float = 0


class LLMStat(BaseModel):
    """LLM 调用统计"""
    call_count: int = 0
    avg_latency_ms: float = 0
    p50_ms: float = 0
    p90_ms: float = 0
    avg_prompt_tokens: int = 0
    avg_completion_tokens: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0


class StageBreakdown(BaseModel):
    """阶段耗时分布"""
    trace_id: str
    stages: Dict[str, float] = {}


class SlowTraceItem(BaseModel):
    """慢 trace 条目"""
    trace_id: str
    session_id: str = ""
    duration_ms: float = 0
    mode: str = ""
    agents_involved: List[str] = []
    question_summary: str = ""


class ErrorTraceItem(BaseModel):
    """错误 trace 条目"""
    trace_id: str
    session_id: str = ""
    duration_ms: float = 0
    mode: str = ""
    question_summary: str = ""
    start_time: str = ""