"""test_swarm/test_events.py — Event 数据模型与 EventType 枚举测试"""

import pytest
from datetime import datetime
from mediZJ.swarm.events import Event, EventType


class TestEventType:
    def test_all_event_types_are_enum(self):
        assert EventType.TASK_DECOMPOSED.value == "task_decomposed"
        assert EventType.SWARM_STARTED.value == "swarm_started"
        assert EventType.SUBTASK_COMPLETED.value == "subtask_completed"

    def test_agent_thinking_types(self):
        assert EventType.AGENT_THINKING is not None
        assert EventType.AGENT_TOOL_STEP is not None
        assert EventType.AGENT_THINKING_DONE is not None

    def test_questionnaire_event_type(self):
        assert EventType.AGENT_QUESTIONNAIRE.value == "agent_questionnaire"


class TestEvent:
    def test_default_broadcast(self):
        evt = Event(type=EventType.SWARM_STARTED, source_agent="lead", data={})
        assert evt.target_agents is None  # 广播
        assert evt.is_for_agent("any_agent") is True

    def test_targeted_event(self):
        evt = Event(
            type=EventType.AGENT_QUESTION,
            source_agent="lead",
            data={"q": "test"},
            target_agents=["diag_agent"],
        )
        assert evt.is_for_agent("diag_agent") is True
        assert evt.is_for_agent("other_agent") is False

    def test_to_dict(self):
        evt = Event(type=EventType.SWARM_COMPLETED, source_agent="coord", data={"ok": True})
        d = evt.to_dict()
        assert d["type"] == "swarm_completed"
        assert d["source_agent"] == "coord"
        assert d["data"] == {"ok": True}
        assert "id" in d
        assert "timestamp" in d
