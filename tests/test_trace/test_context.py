"""test_trace/test_context.py — traced_span 上下文管理器测试"""

import pytest
from unittest.mock import patch

from mediZJ.trace.models import Span, SpanType, SpanStatus
from mediZJ.trace.context import (
    _current_trace_id, _current_span_stack,
    get_current_trace_id, get_current_span_id, traced_span,
)


@pytest.fixture(autouse=True)
def _clear_trace_context():
    """每个测试前后清除 trace contextvars。"""
    _current_trace_id.set(None)
    _current_span_stack.set([])
    yield
    _current_trace_id.set(None)
    _current_span_stack.set([])


class TestTraceIdFunctions:
    def test_no_trace_id_by_default(self):
        assert get_current_trace_id() is None

    def test_no_span_id_by_default(self):
        assert get_current_span_id() is None


class TestTracedSpanBasic:
    def test_basic_context_creates_span(self):
        with traced_span(SpanType.TOOL, name="test-tool") as span:
            assert span is not None
            assert span.span_type == SpanType.TOOL
            assert span.name == "test-tool"
            assert span.id
            assert span.timing.duration_ms is None  # 尚未 finish

    def test_span_timing_recorded_on_exit(self):
        with traced_span(SpanType.TOOL, name="timed-tool") as span:
            pass
        assert span.timing.duration_ms is not None
        assert span.timing.duration_ms >= 0

    def test_trace_span_sets_trace_id(self):
        with traced_span(SpanType.TRACE, name="request") as span:
            assert span.trace_id == span.id  # 根 span 以自身 ID 为 trace_id
            assert get_current_trace_id() == span.id

    def test_nested_span_parent_child(self):
        with traced_span(SpanType.TRACE, name="root") as root:
            assert get_current_span_id() == root.id
            with traced_span(SpanType.AGENT, name="agent") as agent:
                assert agent.parent_id == root.id
                assert get_current_span_id() == agent.id
                with traced_span(SpanType.LLM, name="llm") as llm:
                    assert llm.parent_id == agent.id
                    assert get_current_span_id() == llm.id

    def test_trace_id_cleared_after_root_exit(self):
        with traced_span(SpanType.TRACE, name="root") as span:
            assert get_current_trace_id() is not None
        assert get_current_trace_id() is None


class TestTracedSpanError:
    def test_error_is_recorded_on_exception(self):
        try:
            with traced_span(SpanType.TOOL, name="failing-tool") as span:
                raise ValueError("tool error")
        except ValueError:
            pass
        assert span.status == SpanStatus.ERROR
        assert "tool error" in span.error_message

    def test_exception_is_not_suppressed(self):
        with pytest.raises(ValueError, match="propagate"):
            with traced_span(SpanType.TOOL, name="failing"):
                raise ValueError("propagate")


class TestAsyncTracedSpan:
    @pytest.mark.asyncio
    async def test_async_context(self):
        async with traced_span(SpanType.LLM, name="async-llm") as span:
            assert span.span_type == SpanType.LLM
        assert span.timing.duration_ms is not None

    @pytest.mark.asyncio
    async def test_async_context_error_recording(self):
        with pytest.raises(ValueError, match="async error"):
            async with traced_span(SpanType.TOOL, name="async-tool") as span:
                raise ValueError("async error")
        assert span.status == SpanStatus.ERROR
