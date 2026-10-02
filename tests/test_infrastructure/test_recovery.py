"""真实基础设施上的恢复、隔离和租约验证。"""

import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing import TypedDict

from mediZJ.api.models.chat import ChatRequest
from mediZJ.api.services.run_service import cancel_run, create_run, get_run
from mediZJ.infrastructure.database import (
    transaction,
)
from mediZJ.infrastructure.jobs import LeaseLost, assert_lease, claim, enqueue, finish
from mediZJ.infrastructure.settings import get_settings
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.short_term import ShortTermMemory

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


@pytest_asyncio.fixture
async def infrastructure(mysql_infrastructure):
    yield


async def test_run_idempotency_and_session_conflict(infrastructure):
    user = await SessionDB().get_or_create_user("test-" + uuid.uuid4().hex)
    request = ChatRequest(question="测试", session_id=uuid.uuid4().hex)
    key = uuid.uuid4().hex
    first = await create_run(request, user["user_id"], key)
    second = await create_run(request, user["user_id"], key)
    assert first["run_id"] == second["run_id"]
    with pytest.raises(HTTPException) as error:
        await create_run(request, user["user_id"], uuid.uuid4().hex)
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        await get_run(first["run_id"], "another-user")
    assert error.value.status_code == 404
    await cancel_run(first["run_id"], user["user_id"])
    assert (await get_run(first["run_id"], user["user_id"]))["status"] == "cancelled"


async def test_expired_lease_fences_old_worker(infrastructure):
    kind = "test-" + uuid.uuid4().hex[:20]
    await enqueue(kind, kind, {})
    first = await claim(kind, "first")
    async with transaction() as conn:
        await conn.execute(
            "UPDATE jobs SET lease_until=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) "
            "WHERE job_id=%s",
            (first["job_id"],),
        )
    second = await claim(kind, "second")
    assert second["attempts"] == 2
    assert second["token"] > first["token"]
    async with transaction() as conn:
        with pytest.raises(LeaseLost):
            await assert_lease(conn, first)
        await assert_lease(conn, second)


async def test_redis_cas_rebuild_and_user_isolation(infrastructure):
    session = uuid.uuid4().hex
    memory = ShortTermMemory("first")
    messages = [{"id": 10, "role": "user", "content": "确认历史"}]
    assert await memory.restore_session(session, messages)
    history, revision = await memory._read(session)
    assert not await memory.restore_session(session, messages)
    assert await memory._write(history, revision)
    assert not await memory._write(history, revision)
    assert await ShortTermMemory("second").get_session(session) is None
    await memory.clear_session(session)
    assert await memory.restore_session(session, messages)


class GraphState(TypedDict):
    answer: str


def ask(state: GraphState):
    return {"answer": interrupt({"question": "确认"})}


async def test_mysql_interrupt_resumes_across_connections(infrastructure):
    builder = StateGraph(GraphState)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    async with AsyncMySaver.from_conn_string(get_settings().mysql_url) as saver:
        await saver.setup()
        graph = builder.compile(checkpointer=saver)
        assert "__interrupt__" in await graph.ainvoke({"answer": ""}, config)
    async with AsyncMySaver.from_conn_string(get_settings().mysql_url) as saver:
        graph = builder.compile(checkpointer=saver)
        result = await graph.ainvoke(Command(resume="已确认"), config)
        assert result["answer"] == "已确认"


async def test_final_message_result_and_done_commit_together(
    infrastructure, monkeypatch
):
    from types import SimpleNamespace
    from mediZJ.api.services import chat_service, run_service
    from mediZJ.swarm.events import Event, EventType

    class Coordinator:
        def __init__(self, user_id):
            self.user_id = user_id

        def _init_trace(self, run_id):
            return None

        async def _flush_trace(self, *args):
            pass

        async def build_graph(self, event_callback, **kwargs):
            class Graph:
                async def aget_state(self, config):
                    return SimpleNamespace(values={}, next=("answer",))

                async def ainvoke(self, state, config, **kwargs):
                    event_callback(
                        Event(EventType.AGENT_CONTENT_DELTA, "test", {"token": "测试"})
                    )
                    return {"final_answer": "测试回答"}

            return Graph()

        def build_initial_state(self, question, context, session_id, started, **kwargs):
            return {"start_time": started.isoformat()}

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

    async def verify(question, result):
        return result

    monkeypatch.setattr(run_service, "SwarmCoordinator", Coordinator)
    monkeypatch.setattr(chat_service, "_verify_final_result", verify)
    user = await SessionDB().get_or_create_user("test-" + uuid.uuid4().hex)
    run = await create_run(ChatRequest(question="测试"), user["user_id"], None)
    job = await claim("chat", "test-worker")
    assert job["payload"]["run_id"] == run["run_id"]
    await run_service.execute_run(job)
    await finish(job)
    result = await get_run(run["run_id"], user["user_id"])
    assert result["status"] == "completed"
    assert result["result"]["answer"] == "测试回答"
    events = await run_service.read_events(run["run_id"], user["user_id"], 0)
    assert events[-1]["event"] == "done"
    messages = await SessionDB().get_recent_turns(
        run["session_id"], user["user_id"], None
    )
    assert [item["role"] for item in messages] == ["user", "assistant"]


async def test_answer_persists_once_and_rejects_conflicting_resubmission(
    infrastructure,
):
    import asyncio
    from mediZJ.api.services.run_service import answer_run

    user = await SessionDB().get_or_create_user("test-" + uuid.uuid4().hex)
    run = await create_run(ChatRequest(question="测试问卷"), user["user_id"], None)
    questionnaire = uuid.uuid4().hex
    async with transaction() as conn:
        await conn.execute(
            "UPDATE chat_runs SET status='waiting_answer' WHERE run_id=%s",
            (run["run_id"],),
        )
        await conn.execute(
            "INSERT INTO questionnaires VALUES (%s,%s,'{}',NULL,'pending',"
            "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 HOUR))",
            (questionnaire, run["run_id"]),
        )
    args = (
        run["run_id"],
        run["session_id"],
        questionnaire,
        {"q0": "同意"},
        user["user_id"],
    )
    await asyncio.gather(answer_run(*args), answer_run(*args))
    with pytest.raises(HTTPException) as error:
        await answer_run(
            run["run_id"],
            run["session_id"],
            questionnaire,
            {"q0": "不同"},
            user["user_id"],
        )
    assert error.value.status_code == 409
    async with transaction() as conn:
        row = (
            await conn.execute(
                "SELECT status,answers FROM questionnaires WHERE questionnaire_id=%s",
                (questionnaire,),
            )
        ).fetchone()
    assert row["status"] == "answered"
    await cancel_run(run["run_id"], user["user_id"])


async def test_full_queue_rejects_before_creating_job(infrastructure, monkeypatch):
    from mediZJ.api.services import run_service

    settings = get_settings().model_copy(update={"run_queue_limit": 1})
    monkeypatch.setattr(run_service, "get_settings", lambda: settings)
    user = await SessionDB().get_or_create_user("test-" + uuid.uuid4().hex)
    first = await create_run(ChatRequest(question="排队测试"), user["user_id"], None)
    with pytest.raises(HTTPException) as error:
        await create_run(ChatRequest(question="队列已满"), user["user_id"], None)
    assert error.value.status_code == 503
    assert error.value.headers["Retry-After"] == "5"
    await cancel_run(first["run_id"], user["user_id"])
