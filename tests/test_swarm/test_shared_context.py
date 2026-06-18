"""test_swarm/test_shared_context.py — SharedContext、SubTask、Contribution 测试"""

import pytest
from mediZJ.swarm.shared_context import SharedContext, SubTask, Contribution, TaskStatus
from mediZJ.swarm.events import Event, EventType


class TestTaskStatus:
    def test_enum_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.CLAIMED.value == "claimed"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"


class TestSubTask:
    @pytest.fixture
    def subtask(self):
        return SubTask(
            id="sub-1",
            type="diagnosis",
            description="诊断糖尿病风险",
            assigned_agent="diagnostic_agent",
        )

    def test_initial_state(self, subtask):
        assert subtask.status == TaskStatus.PENDING
        assert subtask.can_be_executed() is True
        assert subtask.result is None

    def test_start(self, subtask):
        subtask.start()
        assert subtask.status == TaskStatus.IN_PROGRESS
        assert subtask.started_at is not None
        assert subtask.can_be_executed() is False

    def test_cannot_start_non_pending(self, subtask):
        subtask.complete({"ok": True})
        with pytest.raises(ValueError, match="cannot be started"):
            subtask.start()

    def test_complete(self, subtask):
        result = {"diagnosis": "low risk"}
        subtask.complete(result)
        assert subtask.status == TaskStatus.COMPLETED
        assert subtask.result == result
        assert subtask.completed_at is not None

    def test_fail(self, subtask):
        subtask.fail("timeout")
        assert subtask.status == TaskStatus.FAILED
        assert subtask.result == {"error": "timeout"}

    def test_dependencies(self):
        subtask = SubTask(
            id="sub-2",
            type="research",
            description="research task",
            assigned_agent="research_agent",
            dependencies=["sub-1"],
        )
        assert subtask.dependencies == ["sub-1"]


class TestContribution:
    def test_default_confidence(self):
        c = Contribution(agent_id="a1", subtask_id="s1", result={"ok": True})
        assert c.confidence == 1.0

    def test_custom_confidence(self):
        c = Contribution(agent_id="a1", subtask_id="s1", result={"ok": True}, confidence=0.8)
        assert c.confidence == 0.8


class TestSharedContextBasics:
    @pytest.fixture
    def ctx(self):
        return SharedContext(session_id="test-session")

    def test_session_id(self, ctx):
        assert ctx.session_id == "test-session"

    def test_default_id_generated(self):
        ctx = SharedContext()
        assert ctx.session_id

    def test_data_set_get(self, ctx):
        ctx.set_data("key1", "value1")
        assert ctx.get_data("key1") == "value1"
        assert ctx.get_data("nonexistent", "default") == "default"

    def test_publish_event_adds_to_history(self, ctx):
        ctx.publish_event(Event(type=EventType.SWARM_STARTED, source_agent="lead", data={}))
        assert len(ctx.events) == 1

    def test_on_event_callback_invoked(self, ctx):
        called = []
        ctx.on_event_callback = lambda e: called.append(e.type.value)
        ctx.publish_event(Event(type=EventType.CONTEXT_UPDATED, source_agent="sys", data={}))
        assert called == ["context_updated"]

    def test_callback_errors_are_silent(self, ctx):
        ctx.on_event_callback = lambda e: (_ for _ in ()).throw(RuntimeError("bad"))
        # 不应抛出异常
        ctx.publish_event(Event(type=EventType.SWARM_STARTED, source_agent="lead", data={}))


class TestSharedContextSubtaskManagement:
    @pytest.fixture
    def ctx(self):
        return SharedContext()

    def test_add_subtask(self, ctx):
        st = SubTask(id="s1", type="consult", description="咨询", assigned_agent="consult_agent")
        ctx.add_subtask(st)
        assert ctx.get_subtask("s1") is st
        assert len(ctx.task_decomposition) == 1

    def test_get_subtasks_for_agent(self, ctx):
        st1 = SubTask(id="s1", type="diag", description="d", assigned_agent="diag_agent")
        st2 = SubTask(id="s2", type="consult", description="c", assigned_agent="other_agent")
        ctx.add_subtask(st1)
        ctx.add_subtask(st2)
        tasks = ctx.get_subtasks_for_agent("diag_agent")
        assert len(tasks) == 1
        assert tasks[0].id == "s1"

    def test_start_subtask(self, ctx):
        st = SubTask(id="s1", type="consult", description="c", assigned_agent="consult_agent")
        ctx.add_subtask(st)
        assert ctx.start_subtask("s1") is True
        assert st.status == TaskStatus.IN_PROGRESS

    def test_start_nonexistent_returns_false(self, ctx):
        assert ctx.start_subtask("no-such-task") is False

    def test_complete_subtask(self, ctx):
        st = SubTask(id="s1", type="consult", description="c", assigned_agent="consult_agent")
        ctx.add_subtask(st)
        ctx.complete_subtask("s1", "consult_agent", {"answer": "test"}, confidence=0.9)
        assert st.status == TaskStatus.COMPLETED
        assert len(ctx.agent_contributions["consult_agent"]) == 1

    def test_complete_wrong_agent_raises(self, ctx):
        st = SubTask(id="s1", type="consult", description="c", assigned_agent="consult_agent")
        ctx.add_subtask(st)
        with pytest.raises(ValueError, match="not assigned"):
            ctx.complete_subtask("s1", "wrong_agent", {"answer": "test"})

    def test_complete_nonexistent_raises(self, ctx):
        with pytest.raises(ValueError, match="not found"):
            ctx.complete_subtask("no-such", "consult_agent", {"answer": "test"})

    def test_data_event_emitted(self, ctx):
        ctx.set_data("important", "data")
        assert ctx.get_data("important") == "data"
        # set_data 发布 CONTEXT_UPDATED 事件
        assert any(e.type == EventType.CONTEXT_UPDATED for e in ctx.events)


class TestAllSubtasksCompleted:
    def test_empty_context_not_completed(self, ctx_fixture=None):
        ctx = SharedContext()
        assert ctx.is_all_subtasks_completed() is False

    def test_single_task_completed(self):
        ctx = SharedContext()
        st = SubTask(id="s1", type="diag", description="d", assigned_agent="diag")
        ctx.add_subtask(st)
        ctx.complete_subtask("s1", "diag", {"ok": True})
        assert ctx.is_all_subtasks_completed() is True

    def test_mixed_completion(self):
        ctx = SharedContext()
        st1 = SubTask(id="s1", type="diag", description="d1", assigned_agent="diag")
        st2 = SubTask(id="s2", type="consult", description="d2", assigned_agent="consult")
        ctx.add_subtask(st1)
        ctx.add_subtask(st2)
        ctx.complete_subtask("s1", "diag", {"ok": True})
        assert ctx.is_all_subtasks_completed() is False
        ctx.complete_subtask("s2", "consult", {"ok": True})
        assert ctx.is_all_subtasks_completed() is True


class TestGetSummary:
    def test_summary(self):
        ctx = SharedContext(session_id="sess-1")
        st = SubTask(id="s1", type="diag", description="d", assigned_agent="diag")
        ctx.add_subtask(st)
        ctx.complete_subtask("s1", "diag", {"ok": True})
        summary = ctx.get_summary()
        assert summary["session_id"] == "sess-1"
        assert summary["total_subtasks"] == 1
        assert summary["completed_subtasks"] == 1


class TestGetContributions:
    def test_get_all_contributions(self):
        ctx = SharedContext()
        st1 = SubTask(id="s1", type="a", description="a", assigned_agent="agent1")
        st2 = SubTask(id="s2", type="b", description="b", assigned_agent="agent2")
        ctx.add_subtask(st1)
        ctx.add_subtask(st2)
        ctx.complete_subtask("s1", "agent1", {"result": 1})
        ctx.complete_subtask("s2", "agent2", {"result": 2})
        all_contribs = ctx.get_contributions()
        assert len(all_contribs) == 2

    def test_get_contributions_by_agent(self):
        ctx = SharedContext()
        st = SubTask(id="s1", type="a", description="a", assigned_agent="agent1")
        ctx.add_subtask(st)
        ctx.complete_subtask("s1", "agent1", {"result": 1})
        contribs = ctx.get_contributions(agent_id="agent1")
        assert len(contribs) == 1
        assert contribs[0].agent_id == "agent1"

    def test_get_contributions_by_subtask_id(self):
        ctx = SharedContext()
        st = SubTask(id="s1", type="a", description="a", assigned_agent="agent1")
        ctx.add_subtask(st)
        ctx.complete_subtask("s1", "agent1", {"result": 1})
        contribs = ctx.get_contributions(subtask_id="s1")
        assert len(contribs) == 1
