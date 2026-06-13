"""test_core/test_state_manager.py — StateManager、AgentState、TaskStatus 测试"""

import pytest
from datetime import datetime
from core.state_manager import StateManager, AgentState, TaskStatus


class TestTaskStatus:
    def test_enum_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"


class TestAgentState:
    def test_default_state(self):
        state = AgentState(task_id="t1", agent_id="a1")
        assert state.status == TaskStatus.PENDING
        assert state.iteration == 0
        assert state.max_iterations == 5
        assert state.final_result is None
        assert state.error is None

    def test_is_completed_completed(self):
        state = AgentState(task_id="t1", agent_id="a1", status=TaskStatus.COMPLETED)
        assert state.is_completed() is True

    def test_is_completed_failed(self):
        state = AgentState(task_id="t1", agent_id="a1", status=TaskStatus.FAILED)
        assert state.is_completed() is True

    def test_is_completed_pending(self):
        state = AgentState(task_id="t1", agent_id="a1", status=TaskStatus.PENDING)
        assert state.is_completed() is False

    def test_should_continue_in_progress(self):
        state = AgentState(task_id="t1", agent_id="a1", status=TaskStatus.IN_PROGRESS, iteration=2)
        assert state.should_continue() is True

    def test_should_continue_at_max_iterations(self):
        state = AgentState(task_id="t1", agent_id="a1", status=TaskStatus.IN_PROGRESS, iteration=5)
        assert state.should_continue() is False

    def test_should_continue_not_in_progress(self):
        state = AgentState(task_id="t1", agent_id="a1", status=TaskStatus.PENDING)
        assert state.should_continue() is False

    def test_add_intermediate_result(self):
        state = AgentState(task_id="t1", agent_id="a1")
        state.add_intermediate_result({"step": 1})
        assert len(state.intermediate_results) == 1
        assert state.intermediate_results[0]["result"] == {"step": 1}
        assert state.intermediate_results[0]["iteration"] == 0

    def test_mark_completed(self):
        state = AgentState(task_id="t1", agent_id="a1")
        state.mark_completed({"answer": "done"})
        assert state.status == TaskStatus.COMPLETED
        assert state.final_result == {"answer": "done"}

    def test_mark_failed(self):
        state = AgentState(task_id="t1", agent_id="a1")
        state.mark_failed("timeout")
        assert state.status == TaskStatus.FAILED
        assert state.error == "timeout"


class TestStateManager:
    @pytest.fixture
    def sm(self):
        return StateManager()

    def test_create_state(self, sm):
        state = sm.create_state("t1", "a1", {"question": "test"})
        assert state.task_id == "t1"
        assert state.agent_id == "a1"
        assert state.max_iterations == 5
        assert sm.get_state("t1") is state

    def test_create_state_custom_iterations(self, sm):
        state = sm.create_state("t1", "a1", {}, max_iterations=3)
        assert state.max_iterations == 3

    def test_get_nonexistent_state(self, sm):
        assert sm.get_state("no-exist") is None

    def test_update_state(self, sm):
        sm.create_state("t1", "a1", {})
        sm.update_state("t1", iteration=3, status=TaskStatus.IN_PROGRESS)
        state = sm.get_state("t1")
        assert state.iteration == 3
        assert state.status == TaskStatus.IN_PROGRESS

    def test_delete_state(self, sm):
        sm.create_state("t1", "a1", {})
        sm.delete_state("t1")
        assert sm.get_state("t1") is None

    def test_get_active_tasks(self, sm):
        sm.create_state("t1", "a1", {})
        sm.create_state("t2", "a2", {})
        sm.update_state("t1", status=TaskStatus.IN_PROGRESS)
        active = sm.get_active_tasks()
        assert len(active) == 1
        assert active[0].task_id == "t1"

    def test_get_active_tasks_empty(self, sm):
        sm.create_state("t1", "a1", {})
        active = sm.get_active_tasks()
        assert len(active) == 0

    def test_cleanup_old_states(self, sm):
        sm.create_state("t1", "a1", {})
        # 设置一个很久以前的 updated_at
        state = sm.get_state("t1")
        from datetime import timedelta
        state.updated_at = datetime.now() - timedelta(hours=48)
        sm.cleanup_old_states(hours=24)
        assert sm.get_state("t1") is None
