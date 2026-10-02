"""验证协调器序列化、问卷恢复和异步后处理边界。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.swarm import swarm_coordinator as module
from mediZJ.trace.models import Span, SpanType


@pytest.fixture
def coordinator(monkeypatch):
    profile = MagicMock()
    profile._structured.save_episodic_summary = AsyncMock()
    monkeypatch.setattr(module, "PersonalProfile", lambda **kwargs: profile)
    monkeypatch.setattr(module, "ShortTermMemory", MagicMock())
    monkeypatch.setattr(module, "MedicalMemoryContextBuilder", MagicMock())
    monkeypatch.setattr(module, "LeadAgent", MagicMock())
    monkeypatch.setattr(module, "IntentClassifier", MagicMock())
    monkeypatch.setattr(module, "create_worker", lambda *args: MagicMock())
    monkeypatch.setattr(module, "discover_skills", lambda _: [])
    registry = MagicMock()
    registry.get_skill_names.return_value = ["medical"]
    registry.get_skill_instructions.return_value = "可信医学来源"
    registry.get_skill_tool_names.return_value = ["search"]
    monkeypatch.setattr(module, "ToolRegistry", lambda: registry)
    return module.SwarmCoordinator(llm_client=MagicMock(), user_id="alice")


async def test_skill_activation_exposes_only_known_tools(coordinator):
    registrations = coordinator._tool_registry.register_base_tool.call_args_list
    activate = registrations[0].kwargs["func"]
    assert (await activate("unknown"))["success"] is False
    assert (await activate("medical"))["available_tools"] == ["search"]
    assert coordinator.get_worker("consultation_agent") is not None
    assert coordinator.get_worker("unknown") is None


async def test_graph_interrupt_and_resume_are_distinct(coordinator):
    from langgraph.types import Command

    graph = MagicMock(ainvoke=AsyncMock(return_value={"__interrupt__": ["question"]}))
    assert await coordinator.run_graph(graph, {}, {}) == {"_interrupted": True}
    graph.ainvoke.return_value = {"final_answer": "回答"}
    result = await coordinator.run_graph(graph, {}, {}, resume={"question": "答案"})
    assert result["final_answer"] == "回答"
    assert isinstance(graph.ainvoke.await_args.args[0], Command)
    graph.ainvoke.side_effect = type("GraphInterrupt", (RuntimeError,), {})("问卷")
    assert await coordinator.run_graph(graph, {}, {}) == {"_interrupted": True}
    graph.ainvoke.side_effect = ConnectionError("基础设施故障")
    with pytest.raises(ConnectionError):
        await coordinator.run_graph(graph, {}, {})


async def test_processing_persists_trace_owner_and_serializable_result(
    coordinator, monkeypatch
):
    graph = MagicMock(
        ainvoke=AsyncMock(
            return_value={
                "final_answer": "建议",
                "agents_involved": ["consultation_agent"],
                "usage": {"total_tokens": 12},
                "context": {"applied_experience_ids": ["experience"]},
            }
        )
    )
    coordinator.build_graph = AsyncMock(return_value=graph)
    root = Span(id="root", trace_id="trace", span_type=SpanType.TRACE, name="request")
    collector = MagicMock(
        get_flat_spans=MagicMock(return_value=[root]), flush=AsyncMock()
    )
    monkeypatch.setattr(coordinator, "_init_trace", lambda _: collector)
    result = await coordinator.process("问题", session_id="session", trace_id="trace")
    assert result["answer"] == "建议"
    assert result["applied_experience_ids"] == ["experience"]
    assert root.trace_attrs.user_id == "alice"
    assert root.trace_attrs.total_tokens == 12
    collector.flush.assert_awaited_once_with("trace")
    initial = graph.ainvoke.await_args.args[0]
    assert isinstance(initial["start_time"], str)
    aware = coordinator.compose_result(
        "问题", {}, datetime.now(timezone.utc), "session"
    )
    assert aware["total_time"] >= 0
    graph.ainvoke.side_effect = RuntimeError("模型失败")
    assert "error" in await coordinator.process("问题", session_id="session")
    coordinator._tool_registry = None
    with pytest.raises(RuntimeError, match="LangGraph"):
        await coordinator.process("问题")


@pytest.mark.parametrize("failure", [False, True])
async def test_candidate_extraction_closes_client_after_failure(
    coordinator, monkeypatch, failure
):
    extractor = MagicMock(
        process=AsyncMock(side_effect=RuntimeError("提取失败") if failure else None)
    )
    extractor.jev.close = AsyncMock()
    monkeypatch.setattr(module, "DualMemoryExtractor", lambda *args: extractor)
    if failure:
        with pytest.raises(RuntimeError):
            await coordinator._save_memory_candidates("session", "问题", "回答", {})
    else:
        await coordinator._save_memory_candidates("session", "问题", "回答", {})
    extractor.jev.close.assert_awaited_once()
    await coordinator._save_session_summary(
        "session", "问题", "agent", "回答", datetime.now(), {}, 2
    )
    saved = coordinator.personal_profile._structured.save_episodic_summary
    saved.assert_awaited_once_with(
        session_id="session", user_id="alice", summary="问题：问题\n回答：回答"
    )


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("## 核心建议\n1. 休息\n2. 复诊\n## 参考资料\n1. 来源", ["休息", "复诊"]),
        ("【核心建议】\n1. 休息", ["休息"]),
        ("普通回答", ["请遵循医嘱，注意休息和营养"]),
    ],
)
def test_suggestions_respect_section_boundary(answer, expected):
    assert module.SwarmCoordinator.extract_suggestions(answer) == expected
    assert module.SwarmCoordinator.format_references_section([]) == ""
    text = module.SwarmCoordinator.format_references_section(
        [
            {"index": 1, "filename": "指南"},
            {"index": 2},
        ]
    )
    assert "[1] 指南" in text and "[2]" in text
