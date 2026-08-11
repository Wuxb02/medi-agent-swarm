"""LeadAgent 最终汇总流式输出测试。"""

from mediZJ.core.llm_client import LLMResponse
from mediZJ.swarm.events import EventType
from mediZJ.swarm.lead_agent import LeadAgent
from mediZJ.swarm.shared_context import Contribution, SharedContext


class _StreamingLLM:
    async def chat_with_tools_stream(
        self,
        messages,
        tools=None,
        on_content_token=None,
        **kwargs,
    ):
        del messages, tools, kwargs
        for token in ["最终", "回答"]:
            on_content_token(token)
        return LLMResponse(
            content="最终回答",
            tool_calls=[],
            finish_reason="stop",
        )


async def test_synthesize_results_emits_final_content_tokens():
    context = SharedContext(session_id="stream-test")
    context.agent_contributions["consultation_agent"].append(Contribution(
        agent_id="consultation_agent",
        subtask_id="task-1",
        result={"answer": "Agent 分析"},
    ))
    events = []
    lead_agent = LeadAgent(llm_client=_StreamingLLM())

    answer = await lead_agent.synthesize_results(
        question="我头痛",
        shared_context=context,
        event_callback=events.append,
    )

    assert answer == "最终回答"
    assert [event.type for event in events] == [
        EventType.AGENT_CONTENT_DELTA,
        EventType.AGENT_CONTENT_DELTA,
    ]
    assert [event.data["token"] for event in events] == ["最终", "回答"]
    assert all(event.data["is_final"] is True for event in events)
