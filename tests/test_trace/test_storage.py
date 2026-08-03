"""test_trace/test_storage.py — TraceSqliteStorage 读写测试"""

import pytest
import tempfile
from pathlib import Path

from mediZJ.trace.storage import TraceSqliteStorage
from mediZJ.trace.models import Span, SpanType, TraceAttributes


@pytest.fixture(autouse=True)
def _reset_storage():
    TraceSqliteStorage.reset()
    yield
    TraceSqliteStorage.reset()


@pytest.fixture
def storage():
    """使用临时 SQLite 文件的 TraceSqliteStorage。"""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "test_sessions.db"
        s = TraceSqliteStorage(db_path=str(db_path))
        yield s
        # reset 后下次使用新路径
        TraceSqliteStorage.reset()


class TestEmptyStorage:
    def test_list_traces_empty(self, storage):
        assert storage.list_traces() == []

    def test_count_traces_zero(self, storage):
        assert storage.count_traces() == 0

    def test_get_nonexistent_trace(self, storage):
        assert storage.get_trace("nonexistent") is None


class TestSaveAndRetrieve:
    def test_save_and_get_trace(self, storage):
        root = Span(
            id="root-1",
            trace_id="trace-1",
            span_type=SpanType.TRACE,
            name="request",
            trace_attrs=TraceAttributes(session_id="sess-1", mode="swarm"),
        )
        root.timing.finish()
        spans = [root]
        storage.save(root, spans)

        result = storage.get_trace("trace-1")
        assert result is not None
        assert result["id"] == "root-1"
        assert result["span_type"] == "trace"

    def test_save_and_list_traces(self, storage):
        root = Span(id="r1", trace_id="trace-1", span_type=SpanType.TRACE, name="req1")
        root.timing.finish()
        storage.save(root, [root])

        traces = storage.list_traces()
        assert len(traces) == 1
        assert traces[0]["trace_id"] == "trace-1"

    def test_count_traces(self, storage):
        for i in range(3):
            root = Span(id=f"r{i}", trace_id=f"trace-{i}", span_type=SpanType.TRACE, name="req")
            root.timing.finish()
            storage.save(root, [root])
        assert storage.count_traces() == 3

    def test_delete_trace(self, storage):
        root = Span(id="r1", trace_id="trace-1", span_type=SpanType.TRACE, name="req")
        root.timing.finish()
        storage.save(root, [root])

        assert storage.delete_trace("trace-1") is True
        assert storage.count_traces() == 0
        assert storage.get_trace("trace-1") is None

    def test_delete_nonexistent_trace(self, storage):
        assert storage.delete_trace("no-such-trace") is False


class TestFlatSpans:
    def test_get_flat_spans(self, storage):
        root = Span(id="r1", trace_id="trace-1", span_type=SpanType.TRACE, name="req")
        root.timing.finish()
        child = Span(
            id="c1", trace_id="trace-1", parent_id="r1",
            span_type=SpanType.AGENT, name="agent",
        )
        child.timing.finish()
        storage.save(root, [root, child])

        flat = storage.get_flat_spans("trace-1")
        assert len(flat) == 2
        types = {s["span_type"] for s in flat}
        assert types == {"trace", "agent"}


class TestListTracesFilter:
    def test_filter_by_session(self, storage):
        root1 = Span(
            id="r1", trace_id="trace-1", span_type=SpanType.TRACE, name="req1",
            trace_attrs=TraceAttributes(session_id="sess-a"),
        )
        root1.timing.finish()
        root2 = Span(
            id="r2", trace_id="trace-2", span_type=SpanType.TRACE, name="req2",
            trace_attrs=TraceAttributes(session_id="sess-b"),
        )
        root2.timing.finish()
        storage.save(root1, [root1])
        storage.save(root2, [root2])

        traces_a = storage.list_traces(session_id="sess-a")
        assert len(traces_a) == 1
        assert traces_a[0]["trace_id"] == "trace-1"

    def test_filter_and_read_by_user(self, storage):
        """普通用户只能读取自己的 Trace。"""

        root = Span(
            id="r-user",
            trace_id="trace-user",
            span_type=SpanType.TRACE,
            name="request",
            trace_attrs=TraceAttributes(
                session_id="sess-user",
                user_id="alice",
            ),
        )
        root.timing.finish()
        storage.save(root, [root])

        assert len(storage.list_traces(user_id="alice")) == 1
        assert storage.list_traces(user_id="bob") == []
        assert storage.get_trace("trace-user", user_id="alice") is not None
        assert storage.get_trace("trace-user", user_id="bob") is None
