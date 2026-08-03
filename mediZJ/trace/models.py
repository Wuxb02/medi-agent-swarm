"""Trace 数据模型"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List


class SpanType(Enum):
    """Span 类型枚举"""
    TRACE = "trace"            # 根 Span，每个请求一个
    STAGE = "stage"            # 管道阶段
    AGENT = "agent"            # Agent 执行
    ITERATION = "iteration"    # Think-Act-Observe 迭代
    LLM = "llm"               # LLM API 调用
    TOOL = "tool"             # 工具执行


class SpanStatus(Enum):
    """Span 状态"""
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class SpanTiming:
    """Span 时间信息"""
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    duration_ms: Optional[float] = None

    def finish(self):
        self.end_time = datetime.now()
        self.duration_ms = (self.end_time - self.start_time).total_seconds() * 1000


@dataclass
class TraceAttributes:
    """Trace 级属性"""
    session_id: str = ""
    user_id: str = "default"
    mode: str = ""  # single_agent / swarm / fallback
    question_summary: str = ""  # 前 200 字符
    agents_involved: List[str] = field(default_factory=list)
    total_tokens: int = 0
    subtasks_created: int = 0
    subtasks_completed: int = 0
    timeout_occurred: bool = False


@dataclass
class AgentAttributes:
    """Agent 级属性"""
    agent_id: str = ""
    subtask_id: Optional[str] = None
    subtask_type: Optional[str] = None
    iteration_count: int = 0
    tool_call_count: int = 0
    total_tokens: int = 0


@dataclass
class LLMAttributes:
    """LLM 调用属性"""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    input_messages_summary: Optional[str] = None
    output_content_summary: Optional[str] = None


@dataclass
class ToolAttributes:
    """工具调用属性"""
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    success: bool = True
    error_message: Optional[str] = None


@dataclass
class Span:
    """Trace Span 数据类"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = ""
    parent_id: Optional[str] = None
    span_type: SpanType = SpanType.TRACE
    name: str = ""
    status: SpanStatus = SpanStatus.OK
    error_message: Optional[str] = None
    timing: SpanTiming = field(default_factory=SpanTiming)
    # 按 span_type 填充对应属性
    trace_attrs: Optional[TraceAttributes] = None
    agent_attrs: Optional[AgentAttributes] = None
    llm_attrs: Optional[LLMAttributes] = None
    tool_attrs: Optional[ToolAttributes] = None
    # children 在 flush 时由 parent_id 重建
    children: List["Span"] = field(default_factory=list)
