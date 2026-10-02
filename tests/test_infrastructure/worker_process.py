"""故障测试应用：只替换模型边界，执行、HTTP、检查点与基础设施保持真实。"""

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pymilvus import MilvusClient
import uvicorn

from mediZJ.api.services import chat_service, run_service
from mediZJ.infrastructure.settings import get_settings
from mediZJ.swarm.events import Event, EventType


class State(TypedDict):
    question: str
    start_time: str
    questionnaire: bool
    clarify_pending: dict
    answer: dict
    final_answer: str


class Coordinator:
    def __init__(self, user_id):
        self.user_id = user_id

    def _init_trace(self, run_id):
        return None

    async def _flush_trace(self, *args):
        pass

    async def build_graph(self, event_callback, hitl_enabled, checkpointer):
        async def prepare(state):
            return {
                "clarify_pending": {
                    "questionnaire_id": str(
                        uuid.uuid5(uuid.NAMESPACE_URL, state["question"])
                    ),
                    "questions": [{"id": "q0", "text": "确认"}],
                }
            }

        def ask(state):
            return {"answer": interrupt(state["clarify_pending"])}

        async def answer(state):
            event_callback(
                Event(EventType.AGENT_CONTENT_DELTA, "test", {"token": "开始"})
            )
            await asyncio.sleep(float(os.environ.get("TEST_NODE_DELAY", "0")))
            event_callback(
                Event(EventType.AGENT_CONTENT_DELTA, "test", {"token": "完成"})
            )
            return {"final_answer": "已恢复：" + str(state.get("answer", {}))}

        builder = StateGraph(State)
        builder.add_node("prepare", prepare)
        builder.add_node("ask", ask)
        builder.add_node("answer", answer)
        builder.add_edge(START, "prepare")
        builder.add_conditional_edges(
            "prepare",
            lambda s: "ask" if hitl_enabled and s["questionnaire"] else "answer",
        )
        builder.add_edge("ask", "answer")
        builder.add_edge("answer", END)
        return builder.compile(checkpointer=checkpointer)

    def build_initial_state(self, question, context, session_id, started, **kwargs):
        return {
            "question": question,
            "start_time": started.isoformat(),
            "questionnaire": context.get("questionnaire", False),
        }

    def compose_result(self, question, output, started, session_id, **kwargs):
        return {
            "answer": output["final_answer"],
            "session_id": session_id,
            "trace_id": kwargs["trace_id"],
            "usage": {},
            "mode": "single",
            "suggestions": [],
            "agents_involved": [],
        }


class ProbeStore:
    def __init__(self):
        self.milvus_client = MilvusClient(uri=get_settings().milvus_uri)


async def verify(question, result):
    return result


if __name__ == "__main__":
    from mediZJ.api import main
    import mediZJ.knowledge.milvus_kb as knowledge
    import mediZJ.memory.session_vector_store as vectors

    run_service.SwarmCoordinator = Coordinator
    chat_service._verify_final_result = verify
    knowledge.MedicalKnowledgeBase = ProbeStore
    vectors.SessionVectorStore = ProbeStore

    async def execute(job):
        try:
            await run_service.execute_run(job)
        except Exception:
            import traceback

            traceback.print_exc()
            raise

    main.get_handlers = lambda: {"chat": execute}
    if os.environ.get("TEST_CRASH_ON_INTERRUPT") == "1":
        original = run_service.FencedSaver.aput_writes

        async def crash_after_interrupt(self, config, writes, *args, **kwargs):
            result = await original(self, config, writes, *args, **kwargs)
            if any(channel == "__interrupt__" for channel, _ in writes):
                os._exit(77)
            return result

        run_service.FencedSaver.aput_writes = crash_after_interrupt
    uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="error")
