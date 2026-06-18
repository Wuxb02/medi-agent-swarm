"""基于 contextvars 的 Trace 上下文传播"""
import contextvars
from typing import Optional, List
from .models import Span, SpanType, SpanStatus

# 异步安全的上下文变量
_current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_trace_id", default=None
)
_current_span_stack: contextvars.ContextVar[List[Span]] = contextvars.ContextVar(
    "current_span_stack", default=[]
)


def get_current_trace_id() -> Optional[str]:
    """获取当前 trace ID"""
    return _current_trace_id.get()


def get_current_span_id() -> Optional[str]:
    """获取当前栈顶 span ID"""
    stack = _current_span_stack.get()
    return stack[-1].id if stack else None


class traced_span:
    """上下文管理器：自动管理 span 生命周期和父子关系

    用法::

        with traced_span(SpanType.TOOL, name="search-knowledge") as span:
            span.tool_attrs = ToolAttributes(tool_name="search-knowledge")
            result = await do_tool_call()

        async with traced_span(SpanType.LLM, name="chat_with_tools") as span:
            span.llm_attrs = LLMAttributes(model="gpt-4o")
            response = await llm_client.chat(...)
    """

    def __init__(self, span_type: SpanType, name: str = ""):
        self.span_type = span_type
        self.name = name
        self.span: Optional[Span] = None

    def __enter__(self):
        from .collector import TraceCollector

        parent_id = get_current_span_id()
        trace_id = get_current_trace_id() or ""

        self.span = Span(
            trace_id=trace_id,
            parent_id=parent_id,
            span_type=self.span_type,
            name=self.name,
        )

        # 根 span 设置 trace_id 为自身 ID
        if self.span_type == SpanType.TRACE:
            self.span.trace_id = self.span.id
            _current_trace_id.set(self.span.id)

        # 压栈
        stack = _current_span_stack.get().copy()
        stack.append(self.span)
        _current_span_stack.set(stack)

        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.span:
            self.span.timing.finish()
            if exc_type:
                self.span.status = SpanStatus.ERROR
                self.span.error_message = str(exc_val)

        # 出栈
        stack = _current_span_stack.get().copy()
        if stack:
            stack.pop()
            _current_span_stack.set(stack)

        # 根 span 退出时清除 trace_id
        if self.span_type == SpanType.TRACE:
            _current_trace_id.set(None)

        # 收集到 collector
        if self.span:
            try:
                from .collector import TraceCollector
                TraceCollector().collect(self.span)
            except Exception:
                pass  # collector 未初始化时不报错

        return False  # 不抑制异常

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        return self.__exit__(*args)
