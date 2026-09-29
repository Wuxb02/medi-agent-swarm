"""chat_service per-session 请求互斥的并发测试"""
import asyncio
import json
from types import SimpleNamespace

import pytest

import mediZJ.api.services.chat_service as cs
from mediZJ.api.models.chat import ChatRequest
from mediZJ.swarm.events import Event, EventType


def test_reference_section_only_contains_cited_items():
    """原问答形态：检索十项，正文只引用第一和第四项。"""
    citations = [
        {"index": index, "filename": f"资料{index}.txt"}
        for index in range(1, 11)
    ]
    answer = (
        "头痛应就医[1]，并观察危险信号[1,4]。"
        "\n\n## 参考资料\n\n" + "\n\n".join(
            f"[{index}] 资料{index}.txt" for index in range(1, 11)
        )
    )

    final_answer, final_citations = cs._keep_cited_references(
        answer, citations
    )

    assert [item["index"] for item in final_citations] == [1, 4]
    assert final_answer.count("## 参考资料") == 1
    assert "[2] 资料2.txt" not in final_answer
    assert "[4] 资料4.txt" in final_answer


def test_reference_filter_handles_ranges_and_no_citation():
    citations = [
        {"index": index, "filename": f"资料{index}.txt"}
        for index in range(1, 5)
    ]
    answer, kept = cs._keep_cited_references("观察[2-3]。", citations)
    assert [item["index"] for item in kept] == [2, 3]
    assert "[1] 资料1.txt" not in answer

    answer, kept = cs._keep_cited_references(
        "请就医。\n\n## 参考资料\n\n[1] 资料1.txt", citations
    )
    assert answer == "请就医。"
    assert kept == []


async def test_final_gate_keeps_answer_and_api_citations_in_sync(monkeypatch):
    citations = [
        {"index": 1, "filename": "已引用.txt"},
        {"index": 2, "filename": "未引用.txt"},
    ]

    class FakeVerifier:
        async def verify_and_rewrite(self, _question, _answer, _citations):
            verification = SimpleNamespace(validated_citations=citations)
            verification.to_dict = lambda: {
                "validated_citations": verification.validated_citations
            }
            return "建议就医[1]。\n\n## 参考资料\n\n[2] 未引用.txt", verification

    monkeypatch.setattr(cs, "_get_answer_verifier", lambda: FakeVerifier())
    result = await cs._verify_final_result("头痛", {
        "answer": "待校验回答",
        "citations": citations,
    })
    assert [item["index"] for item in result["citations"]] == [1]
    assert result["verification"]["validated_citations"] == result["citations"]
    assert "未引用.txt" not in result["answer"]


def test_merge_thinking_events_preserves_phase_and_envelope():
    """历史持久化合并后应与实时 SSE 保持相同的事件结构。"""
    events = [
        {
            "event": "agent_thinking",
            "data": {
                "source_agent": "lead_agent",
                "timestamp": "2026-08-13T01:00:00",
                "data": {
                    "content": "正在综合",
                    "iteration": 2,
                    "phase": "synthesize",
                    "title": "结果汇总",
                    "status": "running",
                },
            },
        },
        {
            "event": "agent_thinking",
            "data": {
                "source_agent": "lead_agent",
                "timestamp": "2026-08-13T01:00:01",
                "data": {
                    "content": " Worker 结果",
                    "iteration": 2,
                    "phase": "synthesize",
                    "title": "结果汇总",
                    "status": "completed",
                },
            },
        },
    ]

    merged = cs._merge_thinking_events(events)

    assert len(merged) == 1
    assert merged[0]["data"]["source_agent"] == "lead_agent"
    assert merged[0]["data"]["timestamp"] == "2026-08-13T01:00:00"
    assert merged[0]["data"]["data"] == {
        "content": "正在综合 Worker 结果",
        "iteration": 2,
        "phase": "synthesize",
        "title": "结果汇总",
        "status": "completed",
    }


class _ConcurrencyTracker:
    """记录 process() 的最大并发进入数"""

    def __init__(self):
        self.current = 0
        self.max_concurrent = 0


class FakeCoordinator:
    """替代 SwarmCoordinator：process 内记录并发度并短暂让出"""

    tracker = _ConcurrencyTracker()

    def __init__(self, **kwargs):
        self.ltm_save_task = None

    async def process(self, question, context, session_id):
        t = FakeCoordinator.tracker
        t.current += 1
        t.max_concurrent = max(t.max_concurrent, t.current)
        try:
            await asyncio.sleep(0.05)
            return {
                "answer": "ok",
                "session_id": session_id,
                "suggestions": [],
            }
        finally:
            t.current -= 1


@pytest.fixture
def patched_service(monkeypatch):
    FakeCoordinator.tracker = _ConcurrencyTracker()
    monkeypatch.setattr(cs, "SwarmCoordinator", FakeCoordinator)
    monkeypatch.setattr(
        cs, "_persist_session_turn", lambda *args, **kwargs: None
    )
    return FakeCoordinator.tracker


async def test_same_session_requests_serialized(patched_service):
    """同会话并发请求排队执行：最大并发数为 1"""
    requests = [
        cs.chat_non_stream(ChatRequest(question=f"q{i}", session_id="s-same"))
        for i in range(5)
    ]
    results = await asyncio.gather(*requests)

    assert patched_service.max_concurrent == 1
    assert all(r.answer == "ok" for r in results)


async def test_different_sessions_run_parallel(patched_service):
    """不同会话的请求可并行：最大并发数 > 1"""
    requests = [
        cs.chat_non_stream(ChatRequest(question=f"q{i}", session_id=f"s-{i}"))
        for i in range(3)
    ]
    await asyncio.gather(*requests)

    assert patched_service.max_concurrent > 1


async def test_session_lock_reused(patched_service):
    """同会话返回同一把互斥锁"""
    lock1 = cs._get_session_lock("s-lock")
    lock2 = cs._get_session_lock("s-lock")
    assert lock1 is lock2


class _FakeRequest:
    """模拟永不主动断开的 HTTP 请求"""

    async def is_disconnected(self) -> bool:
        return False


async def test_stream_timeout_returns_friendly_error(monkeypatch):
    """流式处理超时：前端收到非空错误文案而非空字符串"""

    class SlowCoordinator:
        def __init__(self, **kwargs):
            self.ltm_save_task = None

        def build_graph(self, event_callback=None, hitl_enabled=False):
            return object()

        def _init_trace(self, _trace_id):
            return object()

        async def _flush_trace(self, *_args):
            return None

        def build_initial_state(self, question, context, session_id, start_time):
            return {"question": question, "session_id": session_id}

        async def run_graph(self, graph, initial_state, config, resume=None):
            await asyncio.sleep(10)
            return {"answer": "ok", "session_id": initial_state["session_id"],
                    "suggestions": []}

        def compose_result(self, question, result_state, start_time, session_id,
                           trace_id=None):
            result_state["_memory_save_task"] = None
            return result_state

    monkeypatch.setattr(cs, "SwarmCoordinator", SlowCoordinator)
    monkeypatch.setattr(cs, "_persist_session_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(cs, "_REQUEST_TIMEOUT", 0.05)

    chunks = [
        chunk
        async for chunk in cs.chat_stream(
            ChatRequest(question="q", session_id="s-timeout"),
            _FakeRequest(),
        )
    ]
    error_events = [
        json.loads(chunk)["data"]
        for chunk in chunks
        if json.loads(chunk)["event"] == "error"
    ]

    assert len(error_events) == 1
    assert error_events[0]["error"]
    assert "请求处理超时" in error_events[0]["error"]


async def test_multi_round_questionnaire_resume(monkeypatch):
    """多轮问卷：图经历两次 interrupt，两次 answer 入队后恢复，最终 done（不卡死）"""

    class MultiRoundCoordinator:
        """run_graph 首次与首次 resume 返回挂起态，第二次 resume 返回完整结果"""

        trace_flushed = False

        def __init__(self, **kwargs):
            self.ltm_save_task = None
            self._resume_count = 0

        def build_graph(self, event_callback=None, hitl_enabled=False):
            return object()

        def _init_trace(self, _trace_id):
            return object()

        async def _flush_trace(self, *_args):
            type(self).trace_flushed = True

        def build_initial_state(self, question, context, session_id, start_time):
            return {"question": question, "session_id": session_id}

        async def run_graph(self, graph, initial_state, config, resume=None):
            if resume is None or self._resume_count < 2:
                self._resume_count += 1
                return {"_interrupted": True}
            return {
                "answer": "ok",
                "session_id": initial_state["session_id"],
                "suggestions": [],
                "final_answer": "最终回答",
                "usage": {},
                "agents_involved": ["lead_agent"],
                "swarm_enabled": False,
            }

        def compose_result(self, question, result_state, start_time, session_id,
                           trace_id=None):
            result_state["_memory_save_task"] = None
            return result_state

    monkeypatch.setattr(cs, "SwarmCoordinator", MultiRoundCoordinator)
    monkeypatch.setattr(cs, "_persist_session_turn", lambda *args, **kwargs: None)

    # 启动流消费任务，手动喂两次答案
    from mediZJ.api.services.session_runtime import put_answer

    async def _feed_answers(session_id):
        # 等 SSE 进入挂起态后依次放入两份答案
        await asyncio.sleep(0.1)
        put_answer(session_id, {"q0": "35"})
        await asyncio.sleep(0.1)
        put_answer(session_id, {"q0": "头痛一天"})

    feed_task = asyncio.create_task(_feed_answers("s-multi-round"))

    chunks = []
    async for chunk in cs.chat_stream(
        ChatRequest(question="q", session_id="s-multi-round"),
        _FakeRequest(),
    ):
        chunks.append(chunk)

    await feed_task

    events = [json.loads(c)["event"] for c in chunks]
    # 必须走到 done（未卡死）
    assert "done" in events
    done_data = json.loads(chunks[-1])["data"]
    assert done_data["answer"] == "ok"
    assert MultiRoundCoordinator.trace_flushed is True


async def test_stream_only_forwards_final_content_delta(monkeypatch):
    """SSE 只透传最终正文 token，过滤 Worker 中间结果。"""

    class StreamingCoordinator:
        def __init__(self, event_callback=None, **kwargs):
            del kwargs
            self.event_callback = event_callback
            self.ltm_save_task = None

        def build_graph(self, event_callback=None, hitl_enabled=False):
            del hitl_enabled
            self.event_callback = event_callback
            return object()

        def _init_trace(self, _trace_id):
            return object()

        async def _flush_trace(self, *_args):
            return None

        def build_initial_state(self, question, context, session_id, start_time):
            del context, start_time
            return {"question": question, "session_id": session_id}

        async def run_graph(self, graph, initial_state, config, resume=None):
            del graph, config, resume
            self.event_callback(Event(
                type=EventType.AGENT_CONTENT_DELTA,
                source_agent="worker_agent",
                data={"token": "中间结果"},
            ))
            self.event_callback(Event(
                type=EventType.AGENT_CONTENT_DELTA,
                source_agent="lead_agent",
                data={"token": "最终回答", "is_final": True},
            ))
            await asyncio.sleep(0)
            return {
                "answer": "最终回答",
                "session_id": initial_state["session_id"],
                "suggestions": [],
            }

        def compose_result(
            self,
            question,
            result_state,
            start_time,
            session_id,
            trace_id=None,
        ):
            del question, start_time, session_id, trace_id
            return result_state

    monkeypatch.setattr(cs, "SwarmCoordinator", StreamingCoordinator)
    monkeypatch.setattr(cs, "_persist_session_turn", lambda *args, **kwargs: None)

    chunks = [
        chunk
        async for chunk in cs.chat_stream(
            ChatRequest(question="q", session_id="s-final-stream"),
            _FakeRequest(),
        )
    ]
    events = [json.loads(chunk) for chunk in chunks]
    content_events = [
        event for event in events
        if event["event"] == "agent_content_delta"
    ]

    assert len(content_events) == 1
    assert content_events[0]["data"]["data"]["token"] == "最终回答"
    assert events[-1]["event"] == "done"
