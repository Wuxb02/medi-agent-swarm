"""test_trace/test_models.py — Span, SpanTiming, 属性 dataclass 测试"""

import pytest
from datetime import datetime
from mediZJ.trace.models import (
    Span, SpanType, SpanStatus, SpanTiming,
    TraceAttributes, AgentAttributes, LLMAttributes, ToolAttributes,
)


class TestSpanTiming:
    def test_default_values(self):
        timing = SpanTiming()
        assert timing.start_time is not None
        assert timing.end_time is None
        assert timing.duration_ms is None

    def test_finish(self):
        timing = SpanTiming()
        timing.finish()
        assert timing.end_time is not None
        assert timing.duration_ms is not None
        assert timing.duration_ms >= 0


class TestTraceAttributes:
    def test_default_values(self):
        attrs = TraceAttributes()
        assert attrs.session_id == ""
        assert attrs.mode == ""
        assert attrs.agents_involved == []
        assert attrs.total_tokens == 0
        assert attrs.subtasks_created == 0
        assert attrs.subtasks_completed == 0
        assert attrs.timeout_occurred is False

    def test_full_attributes(self):
        attrs = TraceAttributes(
            session_id="sess-1",
            mode="swarm",
            question_summary="test question",
            agents_involved=["diag", "consult"],
            total_tokens=1500,
            subtasks_created=3,
            subtasks_completed=2,
            timeout_occurred=False,
        )
        assert attrs.session_id == "sess-1"
        assert attrs.mode == "swarm"
        assert len(attrs.agents_involved) == 2
        assert attrs.total_tokens == 1500


class TestAgentAttributes:
    def test_default_values(self):
        attrs = AgentAttributes()
        assert attrs.agent_id == ""
        assert attrs.subtask_id is None
        assert attrs.iteration_count == 0
        assert attrs.tool_call_count == 0

    def test_full_attributes(self):
        attrs = AgentAttributes(
            agent_id="diag_agent",
            subtask_id="sub-1",
            subtask_type="diagnosis",
            iteration_count=3,
            tool_call_count=6,
            total_tokens=800,
        )
        assert attrs.agent_id == "diag_agent"
        assert attrs.subtask_type == "diagnosis"
        assert attrs.iteration_count == 3


class TestLLMAttributes:
    def test_default_values(self):
        attrs = LLMAttributes()
        assert attrs.model == ""
        assert attrs.prompt_tokens == 0
        assert attrs.finish_reason == ""

    def test_full_attributes(self):
        attrs = LLMAttributes(
            model="gpt-4o",
            prompt_tokens=500,
            completion_tokens=300,
            total_tokens=800,
            finish_reason="stop",
        )
        assert attrs.model == "gpt-4o"
        assert attrs.total_tokens == 800


class TestToolAttributes:
    def test_success_default(self):
        attrs = ToolAttributes(tool_name="search")
        assert attrs.success is True
        assert attrs.error_message is None

    def test_error_attributes(self):
        attrs = ToolAttributes(
            tool_name="search",
            success=False,
            error_message="timeout",
        )
        assert attrs.success is False
        assert attrs.error_message == "timeout"


class TestSpan:
    def test_default_span(self):
        span = Span()
        assert span.id
        assert span.trace_id == ""
        assert span.parent_id is None
        assert span.span_type == SpanType.TRACE
        assert span.status == SpanStatus.OK
        assert span.timing is not None
        assert span.children == []

    def test_tool_span(self):
        span = Span(
            span_type=SpanType.TOOL,
            name="search-knowledge",
            tool_attrs=ToolAttributes(tool_name="search-knowledge"),
        )
        assert span.span_type == SpanType.TOOL
        assert span.name == "search-knowledge"
        assert span.tool_attrs.tool_name == "search-knowledge"

    def test_llm_span(self):
        span = Span(
            span_type=SpanType.LLM,
            name="chat_with_tools",
            llm_attrs=LLMAttributes(model="gpt-4o", total_tokens=500),
        )
        assert span.span_type == SpanType.LLM
        assert span.llm_attrs.model == "gpt-4o"

    def test_agent_span(self):
        span = Span(
            span_type=SpanType.AGENT,
            name="diagnostic_agent",
            agent_attrs=AgentAttributes(agent_id="diag", iteration_count=5),
        )
        assert span.span_type == SpanType.AGENT
        assert span.agent_attrs.agent_id == "diag"

    def test_trace_span_with_trace_attrs(self):
        span = Span(
            span_type=SpanType.TRACE,
            trace_attrs=TraceAttributes(session_id="s1", mode="swarm"),
        )
        assert span.trace_attrs.session_id == "s1"

    def test_parent_child_relation(self):
        parent = Span(id="parent-1", trace_id="trace-1")
        child = Span(id="child-1", trace_id="trace-1", parent_id="parent-1")
        parent.children.append(child)
        assert len(parent.children) == 1
        assert parent.children[0].id == "child-1"
        assert parent.children[0].parent_id == "parent-1"

    def test_error_span(self):
        span = Span(status=SpanStatus.ERROR, error_message="something went wrong")
        assert span.status == SpanStatus.ERROR
        assert span.error_message == "something went wrong"
