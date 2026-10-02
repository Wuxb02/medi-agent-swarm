"""test_trace/test_storage.py — TraceStorage 读写测试"""

import pytest

from mediZJ.trace.storage import TraceStorage
from mediZJ.trace.models import Span, SpanType, TraceAttributes

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


@pytest.fixture(autouse=True)
def _reset_storage():
    TraceStorage.reset()
    yield
    TraceStorage.reset()


@pytest.fixture
def storage(mysql_infrastructure):
    return TraceStorage()


class TestEmptyStorage:
    async def test_list_traces_empty(self, storage):
        assert await storage.list_traces() == []

    async def test_count_traces_zero(self, storage):
        assert await storage.count_traces() == 0

    async def test_get_nonexistent_trace(self, storage):
        assert await storage.get_trace("nonexistent") is None


class TestSaveAndRetrieve:
    async def test_save_and_get_trace(self, storage):
        root = Span(
            id="root-1",
            trace_id="trace-1",
            span_type=SpanType.TRACE,
            name="request",
            trace_attrs=TraceAttributes(session_id="sess-1", mode="swarm"),
        )
        root.timing.finish()
        spans = [root]
        await storage.save(root, spans)
        result = await storage.get_trace("trace-1")
        assert result is not None
        assert result["id"] == "root-1"
        assert result["span_type"] == "trace"

    async def test_save_and_list_traces(self, storage):
        root = Span(id="r1", trace_id="trace-1", span_type=SpanType.TRACE, name="req1")
        root.timing.finish()
        await storage.save(root, [root])
        traces = await storage.list_traces()
        assert len(traces) == 1
        assert traces[0]["trace_id"] == "trace-1"

    async def test_count_traces(self, storage):
        for i in range(3):
            root = Span(
                id=f"r{i}", trace_id=f"trace-{i}", span_type=SpanType.TRACE, name="req"
            )
            root.timing.finish()
            await storage.save(root, [root])
        assert await storage.count_traces() == 3

    async def test_delete_trace(self, storage):
        root = Span(id="r1", trace_id="trace-1", span_type=SpanType.TRACE, name="req")
        root.timing.finish()
        await storage.save(root, [root])
        assert await storage.delete_trace("trace-1") is True
        assert await storage.count_traces() == 0
        assert await storage.get_trace("trace-1") is None

    async def test_delete_nonexistent_trace(self, storage):
        assert await storage.delete_trace("no-such-trace") is False


class TestFlatSpans:
    async def test_get_flat_spans(self, storage):
        root = Span(id="r1", trace_id="trace-1", span_type=SpanType.TRACE, name="req")
        root.timing.finish()
        child = Span(
            id="c1",
            trace_id="trace-1",
            parent_id="r1",
            span_type=SpanType.AGENT,
            name="agent",
        )
        child.timing.finish()
        await storage.save(root, [root, child])
        flat = await storage.get_flat_spans("trace-1")
        assert len(flat) == 2
        types = {s["span_type"] for s in flat}
        assert types == {"trace", "agent"}


class TestListTracesFilter:
    async def test_filter_by_session(self, storage):
        root1 = Span(
            id="r1",
            trace_id="trace-1",
            span_type=SpanType.TRACE,
            name="req1",
            trace_attrs=TraceAttributes(session_id="sess-a"),
        )
        root1.timing.finish()
        root2 = Span(
            id="r2",
            trace_id="trace-2",
            span_type=SpanType.TRACE,
            name="req2",
            trace_attrs=TraceAttributes(session_id="sess-b"),
        )
        root2.timing.finish()
        await storage.save(root1, [root1])
        await storage.save(root2, [root2])
        traces_a = await storage.list_traces(session_id="sess-a")
        assert len(traces_a) == 1
        assert traces_a[0]["trace_id"] == "trace-1"

    async def test_filter_and_read_by_user(self, storage):
        """普通用户只能读取自己的 Trace。"""
        root = Span(
            id="r-user",
            trace_id="trace-user",
            span_type=SpanType.TRACE,
            name="request",
            trace_attrs=TraceAttributes(session_id="sess-user", user_id="alice"),
        )
        root.timing.finish()
        await storage.save(root, [root])
        assert len(await storage.list_traces(user_id="alice")) == 1
        assert await storage.list_traces(user_id="bob") == []
        assert await storage.get_trace("trace-user", user_id="alice") is not None
        assert await storage.get_trace("trace-user", user_id="bob") is None
