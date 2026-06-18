"""test_trace/test_collector.py — TraceCollector 收集器测试"""

import pytest
from unittest.mock import MagicMock, patch

from mediZJ.trace.collector import TraceCollector
from mediZJ.trace.models import Span, SpanType, SpanStatus, SpanTiming


@pytest.fixture(autouse=True)
def _reset_collector():
    TraceCollector.reset()
    yield
    TraceCollector.reset()


class TestSingleton:
    def test_singleton_returns_same_instance(self):
        c1 = TraceCollector()
        c2 = TraceCollector()
        assert c1 is c2

    def test_reset_creates_new_instance(self):
        c1 = TraceCollector()
        TraceCollector.reset()
        c2 = TraceCollector()
        assert c1 is not c2


class TestBeginTrace:
    def test_begin_trace_creates_root_span(self):
        collector = TraceCollector()
        collector.begin_trace("trace-1")
        spans = collector.get_flat_spans("trace-1")
        assert len(spans) == 1
        assert spans[0].id == "trace-1"
        assert spans[0].span_type == SpanType.TRACE
        assert spans[0].name == "request"


class TestCollectSpan:
    def test_collect_adds_span(self):
        collector = TraceCollector()
        collector.begin_trace("trace-1")
        span = Span(trace_id="trace-1", span_type=SpanType.TOOL, name="test")
        collector.collect(span)
        spans = collector.get_flat_spans("trace-1")
        assert len(spans) == 2  # root + tool span

    def test_collect_without_trace_id_ignored(self):
        collector = TraceCollector()
        collector.begin_trace("trace-1")
        span = Span(trace_id="", span_type=SpanType.TOOL, name="orphan")
        collector.collect(span)
        # 不应被添加到任何 trace
        assert collector.get_flat_spans("") == []

    def test_collect_with_new_trace_id_auto_creates(self):
        collector = TraceCollector()
        span = Span(trace_id="new-trace", span_type=SpanType.TOOL, name="auto")
        collector.collect(span)
        spans = collector.get_flat_spans("new-trace")
        assert len(spans) == 1


class TestBuildTree:
    def test_simple_flat_tree(self):
        collector = TraceCollector()
        collector.begin_trace("t1")
        root = collector._build_tree(collector.get_flat_spans("t1"))
        assert root.id == "t1"

    def test_parent_child_tree(self):
        collector = TraceCollector()
        collector.begin_trace("t1")
        child = Span(trace_id="t1", parent_id="t1", span_type=SpanType.AGENT, name="agent")
        collector.collect(child)
        root = collector._build_tree(collector.get_flat_spans("t1"))
        assert len(root.children) == 1
        assert root.children[0].id == child.id


class TestCallbacks:
    def test_add_remove_callback(self):
        collector = TraceCollector()
        collector.begin_trace("t1")

        called = []
        cb = lambda s: called.append(s.id)
        collector.add_span_callback("t1", cb)

        span = Span(trace_id="t1", span_type=SpanType.TOOL, name="cb-test")
        collector.collect(span)
        assert called == [span.id]

        collector.remove_span_callback("t1", cb)
        collector.collect(Span(trace_id="t1", span_type=SpanType.TOOL, name="cb-test2"))
        assert len(called) == 1  # 回调已移除，不再触发

    def test_callback_errors_are_silent(self):
        collector = TraceCollector()
        collector.begin_trace("t1")

        def bad_cb(span):
            raise RuntimeError("callback error")

        collector.add_span_callback("t1", bad_cb)
        span = Span(trace_id="t1", span_type=SpanType.TOOL, name="bad-cb")
        # 不应抛出异常
        collector.collect(span)
