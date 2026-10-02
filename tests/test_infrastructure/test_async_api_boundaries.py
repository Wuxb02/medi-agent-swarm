"""验证异步存储结果和用户权限能够正确穿过 API 边界。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from mediZJ.api.routers import dashboard, traces


@pytest.mark.parametrize(
    "name,method,arguments,key",
    [
        ("get_agent_stats", "get_agent_stats", {"days": 7}, "stats"),
        ("get_tool_stats", "get_tool_stats", {"days": 7}, "stats"),
        ("get_llm_stats", "get_llm_stats", {"days": 7}, "stats"),
        (
            "get_slow_traces",
            "get_slow_traces",
            {"threshold_ms": 1000, "limit": 2},
            "traces",
        ),
        ("get_error_traces", "get_error_traces", {"days": 7, "limit": 2}, "traces"),
    ],
)
async def test_trace_stats_await_mysql_result(
    monkeypatch, name, method, arguments, key
):
    analyze = AsyncMock(return_value=[{"count": 3}])
    monkeypatch.setattr(traces._analyzer, method, analyze)
    result = await getattr(traces, name)(**arguments)
    assert result[key] == [{"count": 3}]
    analyze.assert_awaited_once_with(*arguments.values())


@pytest.mark.parametrize("role,owner", [("user", "alice"), ("admin", None)])
async def test_trace_views_keep_user_scope(monkeypatch, role, owner):
    storage = MagicMock(
        list_traces=AsyncMock(return_value=[]),
        count_traces=AsyncMock(return_value=0),
        get_trace=AsyncMock(return_value={"trace_id": "trace"}),
        get_flat_spans=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(traces, "_storage", storage)
    monkeypatch.setattr(traces._analyzer, "get_waterfall", AsyncMock(return_value={}))
    monkeypatch.setattr(
        traces._analyzer, "get_stage_breakdown", AsyncMock(return_value={})
    )
    user = {"user_id": "alice", "role": role}
    assert (await traces.list_traces(10, 0, "session", user))["total"] == 0
    storage.list_traces.assert_awaited_once_with(
        limit=10, offset=0, session_id="session", user_id=owner
    )
    assert await traces.get_trace("trace", user) == {"trace_id": "trace"}
    assert (await traces.get_trace_spans("trace", user))["count"] == 0
    await traces.get_trace_waterfall("trace", user)
    await traces.get_trace_stages("trace", user)
    storage.get_trace.assert_awaited_with("trace", user_id=owner)
    storage.get_trace.return_value = None
    assert "error" in await traces.get_trace("missing", user)
    with pytest.raises(HTTPException) as failure:
        await traces.get_trace_waterfall("missing", user)
    assert failure.value.status_code == 404


@pytest.mark.parametrize("role,owner", [("user", "alice"), ("admin", None)])
async def test_dashboard_keeps_user_scope(monkeypatch, role, owner):
    stats = AsyncMock(return_value={"total_sessions": 0})
    monkeypatch.setattr(dashboard, "get_dashboard_stats", stats)
    assert await dashboard.get_stats({"user_id": "alice", "role": role}) == {
        "total_sessions": 0
    }
    stats.assert_awaited_once_with(user_id=owner)


@pytest.mark.integration
@pytest.mark.infrastructure
async def test_trace_statistics_and_waterfall_on_real_mysql(mysql_infrastructure):
    from mediZJ.trace.analysis import TraceAnalyzer
    from mediZJ.trace.models import (
        AgentAttributes,
        LLMAttributes,
        Span,
        SpanStatus,
        SpanType,
        ToolAttributes,
        TraceAttributes,
    )
    from mediZJ.trace.storage import TraceStorage

    TraceAnalyzer.reset()
    analyzer = TraceAnalyzer()
    assert await analyzer.get_llm_stats() == {"call_count": 0}
    assert (await analyzer.get_waterfall("missing"))["spans"] == []
    root = Span(
        id="root",
        trace_id="trace",
        span_type=SpanType.TRACE,
        name="request",
        status=SpanStatus.ERROR,
        trace_attrs=TraceAttributes(session_id="session", agents_involved=["agent"]),
    )
    stage = Span(
        id="stage",
        trace_id="trace",
        parent_id="root",
        span_type=SpanType.STAGE,
        name="answer",
    )
    agent = Span(
        id="agent",
        trace_id="trace",
        parent_id="stage",
        span_type=SpanType.AGENT,
        name="agent",
        status=SpanStatus.ERROR,
        agent_attrs=AgentAttributes(agent_id="agent", total_tokens=30),
    )
    tool = Span(
        id="tool",
        trace_id="trace",
        parent_id="agent",
        span_type=SpanType.TOOL,
        name="search",
        status=SpanStatus.ERROR,
        tool_attrs=ToolAttributes(tool_name="search"),
    )
    llm = Span(
        id="llm",
        trace_id="trace",
        parent_id="agent",
        span_type=SpanType.LLM,
        name="model",
        llm_attrs=LLMAttributes(prompt_tokens=20, completion_tokens=10),
    )
    spans = [root, stage, agent, tool, llm]
    for span in spans:
        span.timing.finish()
        span.timing.duration_ms = 100
    await TraceStorage().save(root, spans)
    stats = await analyzer.get_agent_stats()
    assert stats["agent"]["avg_tokens"] == 30
    assert stats["agent"]["success_rate"] == 0
    assert (await analyzer.get_tool_stats())["search"]["success_rate"] == 0
    assert (await analyzer.get_llm_stats())["total_prompt_tokens"] == 20
    assert await analyzer.get_stage_breakdown("trace") == {"answer": 100}
    assert (await analyzer.get_slow_traces(50))[0]["agents_involved"] == ["agent"]
    assert (await analyzer.get_error_traces())[0]["trace_id"] == "trace"
    waterfall = await analyzer.get_waterfall("trace")
    depths = {span["id"]: span["depth"] for span in waterfall["spans"]}
    assert depths["root"] == 0
    assert depths["llm"] == 3


async def test_session_view_preserves_turn_metadata_and_owner(monkeypatch):
    from mediZJ.api.services import session_service

    data = {
        "session_id": "session",
        "created_at": "2026-10-02T00:00:00",
        "total_tokens": 10,
        "messages": [
            {"role": "user", "content": "第一轮", "turn_index": 0},
            {
                "role": "assistant",
                "id": 7,
                "content": "建议",
                "total_time": 2,
                "total_tokens": 10,
                "subtasks_completed": 1,
                "mode": "swarm",
                "agent_events": [{"type": "agent_end"}],
                "suggestions": ["复诊"],
                "agents_involved": ["agent"],
                "citations": [{"index": 1}],
            },
            {"role": "user", "content": "未完成追问", "turn_index": 1},
        ],
    }
    database = MagicMock(
        get_session=AsyncMock(return_value=data),
        count_sessions=AsyncMock(return_value=1),
        list_sessions=AsyncMock(
            return_value=[
                {
                    "session_id": "session",
                    "first_question": "问题",
                    "created_at": data["created_at"],
                    "message_count": 3,
                    "mode": "swarm",
                    "total_tokens": 10,
                }
            ]
        ),
    )
    monkeypatch.setattr(session_service, "_db", database)
    detail = await session_service.get_session_detail("session", "alice")
    assert len(detail.turns) == 2
    assert detail.turns[0].assistant_message["citations"] == [{"index": 1}]
    assert detail.turns[0].assistant_message["assistant_message_id"] == "7"
    assert detail.total_time == 2
    assert (await session_service.list_sessions(user_id="alice"))[0].message_count == 3
    assert await session_service.count_sessions("alice") == 1
    database.get_session.assert_awaited_with("session", user_id="alice")
    database.get_session.return_value = None
    assert await session_service.get_session_detail("session", "bob") is None
    assert session_service._build_detail_from_db({"session_id": "empty"}).turns == []


async def test_dashboard_aggregates_persisted_session_metadata(monkeypatch):
    from types import SimpleNamespace
    from mediZJ.api.models.session import SessionListItem
    from mediZJ.api.services import dashboard_service

    sessions = [
        SessionListItem(
            session_id=str(index),
            created_at="2026-10-02T00:00:00",
            message_count=2,
            mode=mode,
            total_tokens=10,
            parallel_efficiency=0.5,
            information_coverage=0.8,
            redundancy=0.1,
        )
        for index, mode in enumerate(["swarm", "single", "swarm"])
    ]
    monkeypatch.setattr(
        dashboard_service, "list_sessions", AsyncMock(return_value=sessions)
    )
    monkeypatch.setattr(
        dashboard_service,
        "get_session_detail",
        AsyncMock(
            side_effect=[
                SimpleNamespace(agents_involved=["agent", ""], total_time=2),
                None,
                None,
            ]
        ),
    )
    monkeypatch.setattr(
        dashboard_service, "get_knowledge_base_size", AsyncMock(return_value=4)
    )
    stats = await dashboard_service.get_dashboard_stats("alice")
    assert stats.total_sessions == 3 and stats.total_messages == 6
    assert stats.total_tokens == 30 and stats.avg_response_time == 2
    assert stats.swarm_sessions == 2 and stats.knowledge_base_size == 4
    assert stats.agents_usage["consultation_agent"] == 2
    assert stats.avg_parallel_efficiency == 0.5
    monkeypatch.setattr(dashboard_service, "list_sessions", AsyncMock(return_value=[]))
    empty = await dashboard_service.get_dashboard_stats("alice")
    assert empty.avg_response_time == 0 and empty.avg_parallel_efficiency == 0


async def test_governance_retry_maps_business_conflicts_and_scopes_actor(monkeypatch):
    from mediZJ.api.routers import governance

    actor = {"user_id": "admin", "role": "admin"}
    retry = AsyncMock(return_value={"queued": True})
    monkeypatch.setattr("mediZJ.infrastructure.indexing.retry", retry)
    assert await governance.retry_index_job("job", actor) == {"queued": True}
    for error, status in ((LookupError("missing"), 404), (ValueError("conflict"), 409)):
        retry.side_effect = error
        with pytest.raises(HTTPException) as caught:
            await governance.retry_index_job("job", actor)
        assert caught.value.status_code == status
    lifecycle = MagicMock(
        delete_user=AsyncMock(return_value={"deleted": True}),
        prune_expired=AsyncMock(return_value={"pruned": True}),
        retry=AsyncMock(return_value={"queued": True}),
    )
    catalog = MagicMock(get_job=AsyncMock(return_value={"job_id": "job"}))
    monkeypatch.setattr(governance, "DataLifecycleService", lambda: lifecycle)
    monkeypatch.setattr(governance, "KnowledgeCatalog", lambda: catalog)
    await governance.delete_user_data("alice", actor)
    lifecycle.delete_user.assert_awaited_once_with("alice", "admin")
    await governance.prune_expired(actor)
    assert (await governance.get_lifecycle_job("job", actor))["job_id"] == "job"
    await governance.retry_lifecycle_job("job", actor)
    lifecycle.retry.assert_awaited_once_with("job", "admin")
    catalog.get_job.return_value = None
    lifecycle.retry.side_effect = LookupError("missing")
    for call in (governance.get_lifecycle_job, governance.retry_lifecycle_job):
        with pytest.raises(HTTPException) as caught:
            await call("job", actor)
        assert caught.value.status_code == 404


async def test_evolution_routes_await_storage_and_scope_feedback(monkeypatch):
    from mediZJ.api.routers import evolution
    from mediZJ.api.models.evolution import FeedbackRequest, ManualEvaluationRequest

    actor = {"user_id": "alice", "role": "admin"}
    storage = MagicMock()
    for name in (
        "get_feedback",
        "overview",
        "list_evaluations",
        "list_failures",
        "list_experiences",
        "list_releases",
        "list_jobs",
    ):
        setattr(storage, name, AsyncMock(return_value=[]))
    service = MagicMock(
        storage=storage,
        submit_feedback=AsyncMock(return_value={"saved": True}),
        enqueue_manual=AsyncMock(return_value="job"),
    )
    monkeypatch.setattr(evolution, "EvolutionService", lambda: service)
    payload = FeedbackRequest(assistant_message_id=7, rating="like", comment="  建议  ")
    assert await evolution.submit_feedback(payload, actor) == {"saved": True}
    service.submit_feedback.assert_awaited_once_with(7, "alice", "like", [], "建议")
    await evolution.get_feedback(7, actor)
    storage.get_feedback.assert_awaited_once_with(7, "alice")
    await evolution.get_overview(actor)
    await evolution.list_evaluations(10, actor)
    await evolution.list_failures(10, actor)
    await evolution.list_experiences(10, "active", actor)
    await evolution.list_releases(10, actor)
    await evolution.list_jobs(10, "pending", actor)
    assert (
        await evolution.enqueue_evaluation(
            ManualEvaluationRequest(assistant_message_id=7), actor
        )
    )["queued"]
    service.submit_feedback.side_effect = LookupError("missing")
    with pytest.raises(HTTPException) as caught:
        await evolution.submit_feedback(payload, actor)
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        await evolution.list_jobs(10, "invalid", actor)
    assert caught.value.status_code == 422
