"""持久化问答；执行生命周期独立于 HTTP 连接。"""

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver
from langgraph.types import Command

from mediZJ.api.models.chat import ChatRequest
from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.jobs import assert_lease, decode, enqueue
from mediZJ.infrastructure.settings import get_settings
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.short_term import ShortTermMemory
from mediZJ.swarm.swarm_coordinator import SwarmCoordinator

_TERMINAL = {"completed", "failed", "cancelled", "expired"}


async def get_run(run_id: str, user_id: str) -> dict:
    async with transaction() as conn:
        row = (
            await conn.execute(
                "SELECT * FROM chat_runs WHERE run_id=%s AND user_id=%s",
                (run_id, user_id),
            )
        ).fetchone()
        if row is None:
            raise HTTPException(404, "执行不存在")
        result = decode(row)
        questionnaire = (
            await conn.execute(
                "SELECT * FROM questionnaires WHERE run_id=%s AND status='pending'",
                (run_id,),
            )
        ).fetchone()
        result["questionnaire"] = decode(questionnaire)
        return result


async def create_run(
    request: ChatRequest, user_id: str, key: str | None, hitl=True
) -> dict:
    from mediZJ.infrastructure.metrics import increment

    try:
        return await _create_run(request, user_id, key, hitl)
    except HTTPException as exc:
        if exc.status_code in {429, 503}:
            await increment(f"run_rejected_{exc.status_code}")
        raise


async def _create_run(
    request: ChatRequest, user_id: str, key: str | None, hitl=True
) -> dict:
    settings = get_settings()
    if key is not None and (not key or len(key) > 191):
        raise HTTPException(422, "幂等键长度必须为 1 到 191")
    payload = request.model_copy(update={"user_id": user_id}).model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(
            {"request": payload, "hitl": hitl},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    async with transaction() as conn:
        await conn.execute("SELECT name FROM admission WHERE name='runs' FOR UPDATE")
        if key:
            existing = (
                await conn.execute(
                    "SELECT * FROM chat_runs WHERE user_id=%s AND idempotency_key=%s",
                    (user_id, key),
                )
            ).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise HTTPException(409, "幂等键已用于不同请求")
                return decode(existing)
        session_id = payload["session_id"] or uuid.uuid4().hex
        session = await SessionDB().get_session(session_id)
        if session and session["user_id"] != user_id:
            raise HTTPException(404, "会话不存在")
        active = (
            await conn.execute(
                "SELECT run_id FROM chat_runs WHERE active_session=%s",
                (session_id,),
            )
        ).fetchone()
        if active:
            raise HTTPException(409, "会话已有未结束问答")
        count = (
            await conn.execute(
                "SELECT COUNT(*) AS count FROM chat_runs WHERE user_id=%s "
                "AND status IN ('queued','running','waiting_answer')",
                (user_id,),
            )
        ).fetchone()["count"]
        if count >= settings.user_run_limit:
            raise HTTPException(
                429, "用户未结束问答已达上限", headers={"Retry-After": "5"}
            )
        queued = (
            await conn.execute(
                "SELECT COUNT(*) AS count FROM chat_runs WHERE status='queued'",
            )
        ).fetchone()["count"]
        if queued >= settings.run_queue_limit:
            raise HTTPException(503, "问答队列已满", headers={"Retry-After": "5"})
        run_id = uuid.uuid4().hex
        payload["session_id"] = session_id
        await conn.execute(
            "INSERT INTO chat_runs (run_id,session_id,user_id,request,status,hitl,"
            "idempotency_key,request_hash,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,'queued',%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
            (run_id, session_id, user_id, json.dumps(payload), int(hitl), key, digest),
        )
        await enqueue("chat", f"chat:{run_id}:initial", {"run_id": run_id})
    return await get_run(run_id, user_id)


async def append_event(run_id: str, event: str, data: dict, job=None):
    async with transaction() as conn:
        if job:
            await assert_lease(conn, job)
        row = (
            await conn.execute(
                "SELECT seq,status FROM chat_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            )
        ).fetchone()
        seq = row["seq"] + 1
        await conn.execute("UPDATE chat_runs SET seq=%s WHERE run_id=%s", (seq, run_id))
        await conn.execute(
            "INSERT INTO run_events VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(6))",
            (run_id, seq, event, json.dumps(data, ensure_ascii=False, default=str)),
        )
        return seq


async def read_events(run_id: str, user_id: str, after: int):
    await get_run(run_id, user_id)
    async with transaction() as conn:
        rows = (
            await conn.execute(
                "SELECT * FROM run_events WHERE run_id=%s AND seq>%s ORDER BY seq LIMIT 128",
                (run_id, after),
            )
        ).fetchall()
        return [decode(r) for r in rows]


async def answer_run(run_id, session_id, questionnaire_id, answers, user_id):
    async with transaction() as conn:
        run = (
            await conn.execute(
                "SELECT * FROM chat_runs WHERE run_id=%s AND user_id=%s AND session_id=%s FOR UPDATE",
                (run_id, user_id, session_id),
            )
        ).fetchone()
        if run is None:
            raise HTTPException(404, "执行不存在")
        row = (
            await conn.execute(
                "SELECT *,expires_at<=UTC_TIMESTAMP(6) AS expired FROM questionnaires "
                "WHERE questionnaire_id=%s AND run_id=%s FOR UPDATE",
                (questionnaire_id, run_id),
            )
        ).fetchone()
        if row is None:
            raise HTTPException(404, "问卷不存在")
        row = decode(row)
        if row["status"] == "answered":
            if row["answers"] == answers:
                return
            raise HTTPException(409, "问卷已提交不同答案")
        if row["expired"] or run["status"] != "waiting_answer":
            raise HTTPException(409, "问卷已过期或执行已结束")
        await conn.execute(
            "UPDATE questionnaires SET status='answered',answers=%s WHERE questionnaire_id=%s",
            (json.dumps(answers), questionnaire_id),
        )
        await conn.execute(
            "UPDATE chat_runs SET status='queued',updated_at=UTC_TIMESTAMP(6) WHERE run_id=%s",
            (run_id,),
        )
        await enqueue(
            "chat",
            f"chat:{run_id}:{questionnaire_id}",
            {"run_id": run_id, "questionnaire_id": questionnaire_id},
        )


async def cancel_run(run_id: str, user_id: str):
    await get_run(run_id, user_id)
    async with transaction() as conn:
        row = (
            await conn.execute(
                "SELECT status FROM chat_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            )
        ).fetchone()
        if row["status"] not in _TERMINAL:
            from mediZJ.infrastructure.events import terminate

            await terminate(conn, run_id, "cancelled", "问答已取消")
            await conn.execute(
                "UPDATE jobs SET status='cancelled',token=token+1,lease_until=NULL "
                "WHERE kind='chat' AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.run_id'))=%s "
                "AND status IN ('pending','running')",
                (run_id,),
            )
    return await get_run(run_id, user_id)


class FencedSaver(AsyncMySaver):
    """检查点写入期间锁住租约，阻止旧工作器提交状态。"""

    def __init__(self, conn, job):
        super().__init__(conn)
        self.job = job

    async def aput(self, *args, **kwargs):
        async with transaction() as conn:
            await assert_lease(conn, self.job)
            return await super().aput(*args, **kwargs)

    async def aput_writes(self, *args, **kwargs):
        async with transaction() as conn:
            await assert_lease(conn, self.job)
            return await super().aput_writes(*args, **kwargs)


async def execute_run(job: dict):
    """预算覆盖初始化、模型、验证和最终写入，接管不重置预算。"""
    async with transaction() as conn:
        await assert_lease(conn, job)
        run = (
            await conn.execute(
                "SELECT status,elapsed_seconds,LEAST(%s,TIMESTAMPDIFF(MICROSECOND,"
                "updated_at,UTC_TIMESTAMP(6))/1000000) AS interrupted_seconds "
                "FROM chat_runs WHERE run_id=%s",
                (get_settings().lease_seconds, job["payload"]["run_id"]),
            )
        ).fetchone()
    if run is None or run["status"] in _TERMINAL:
        return
    consumed = run["elapsed_seconds"]
    if run["status"] == "running":
        consumed += float(run["interrupted_seconds"])
    remaining = get_settings().run_timeout - consumed
    if remaining <= 0:
        raise TimeoutError("问答执行预算耗尽")
    from mediZJ.infrastructure.context import execution_budget

    budget_token = execution_budget.set((consumed, asyncio.get_running_loop().time()))
    try:
        await asyncio.wait_for(_execute_run(job), remaining)
    finally:
        execution_budget.reset(budget_token)


async def _execute_run(job: dict):
    run_id = job["payload"]["run_id"]
    async with transaction() as conn:
        await assert_lease(conn, job)
        run = decode(
            (
                await conn.execute(
                    "SELECT * FROM chat_runs WHERE run_id=%s FOR UPDATE",
                    (run_id,),
                )
            ).fetchone()
        )
        if run["status"] in _TERMINAL:
            return
        if run["status"] == "running":
            await conn.execute(
                "UPDATE chat_runs SET elapsed_seconds=elapsed_seconds+LEAST(%s,"
                "TIMESTAMPDIFF(MICROSECOND,updated_at,UTC_TIMESTAMP(6))/1000000) WHERE run_id=%s",
                (get_settings().lease_seconds, run_id),
            )
        await conn.execute(
            "UPDATE chat_runs SET status='running',updated_at=UTC_TIMESTAMP(6) WHERE run_id=%s",
            (run_id,),
        )
    request = ChatRequest(**run["request"])
    history = await SessionDB().get_recent_turns(
        run["session_id"], run["user_id"], None
    )
    await ShortTermMemory(run["user_id"]).restore_session(run["session_id"], history)
    from mediZJ.infrastructure.context import execution_identity

    identity_token = execution_identity.set((run["user_id"], run["session_id"]))
    coordinator = SwarmCoordinator(user_id=run["user_id"])
    trace_collector = coordinator._init_trace(run_id)
    queue = asyncio.Queue(maxsize=256)

    def emit(event):
        if event.type.value != "agent_questionnaire":
            queue.put_nowait(event)

    async def flush_events():
        backlog = []
        while True:
            event = backlog.pop(0) if backlog else await queue.get()
            count = 1
            payload = event.to_dict()
            field = {"agent_content_delta": "token", "agent_thinking": "content"}.get(
                event.type.value
            )
            if field:
                deadline = asyncio.get_running_loop().time() + 0.1
                size = len(str(payload["data"].get(field, "")).encode())
                while size < 4096:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        following = await asyncio.wait_for(queue.get(), remaining)
                    except asyncio.TimeoutError:
                        break
                    if (
                        following.type != event.type
                        or following.source_agent != event.source_agent
                        or any(
                            following.data.get(key) != event.data.get(key)
                            for key in ("iteration", "phase")
                        )
                    ):
                        backlog.append(following)
                        break
                    token = str(following.data.get(field, ""))
                    payload["data"][field] = str(payload["data"].get(field, "")) + token
                    size += len(token.encode())
                    count += 1
            try:
                await append_event(run_id, _event_name(event.type.value), payload, job)
            finally:
                for _ in range(count):
                    queue.task_done()

    writer = asyncio.create_task(flush_events())
    from mediZJ.api.services.chat_service import (
        _verify_final_result,
        _persist_session_turn,
    )

    try:
        async with AsyncMySaver.from_conn_string(get_settings().mysql_url) as saver:
            graph = await coordinator.build_graph(
                event_callback=emit,
                hitl_enabled=bool(run["hitl"]),
                checkpointer=FencedSaver(saver.conn, job),
            )
            config = {"configurable": {"thread_id": run_id}}
            state = await graph.aget_state(config)
            if state.values:
                initial = state.values
            else:
                from mediZJ.evolution import EvolutionService

                runtime_context = await EvolutionService().get_runtime_context(
                    run["user_id"], request.question
                )
                question = request.question
                if request.images:
                    from mediZJ.api.services.image_analyzer import ImageAnalyzer

                    question = await asyncio.wait_for(
                        ImageAnalyzer().analyze(request.images, question),
                        get_settings().run_timeout - run["elapsed_seconds"],
                    )
                initial = coordinator.build_initial_state(
                    question,
                    {**(request.context or {}), **runtime_context},
                    run["session_id"],
                    datetime.now(timezone.utc),
                    trace_id=run_id,
                )
            questionnaire_id = job["payload"].get("questionnaire_id")
            interrupted = any(task.interrupts for task in getattr(state, "tasks", ()))
            if questionnaire_id and interrupted:
                async with transaction() as conn:
                    row = decode(
                        (
                            await conn.execute(
                                "SELECT answers FROM questionnaires WHERE questionnaire_id=%s",
                                (questionnaire_id,),
                            )
                        ).fetchone()
                    )
                graph_input = Command(resume=row["answers"])
            else:
                graph_input = None if state.values else initial
            async with transaction() as conn:
                consumed = (
                    await conn.execute(
                        "SELECT elapsed_seconds+TIMESTAMPDIFF(MICROSECOND,updated_at,UTC_TIMESTAMP(6))"
                        "/1000000 AS seconds FROM chat_runs WHERE run_id=%s",
                        (run_id,),
                    )
                ).fetchone()["seconds"]
            remaining = get_settings().run_timeout - float(consumed)
            if remaining <= 0:
                raise TimeoutError("问答执行预算耗尽")
            output = (
                {**state.values, "__interrupt__": True}
                if interrupted and not questionnaire_id
                else (
                    state.values
                    if state.values and not state.next
                    else await asyncio.wait_for(
                        graph.ainvoke(graph_input, config, durability="sync"), remaining
                    )
                )
            )
            await asyncio.wait_for(queue.join(), timeout=10)
            if writer.done():
                await writer
            if "__interrupt__" in output:
                state = await graph.aget_state(config)
                pending = state.values["clarify_pending"]
                async with transaction() as conn:
                    await assert_lease(conn, job)
                    await conn.execute(
                        "INSERT INTO questionnaires VALUES (%s,%s,%s,NULL,'pending',"
                        "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND)) "
                        "ON DUPLICATE KEY UPDATE questionnaire_id=questionnaire_id",
                        (
                            pending["questionnaire_id"],
                            run_id,
                            json.dumps(pending),
                            get_settings().questionnaire_ttl,
                        ),
                    )
                    await conn.execute(
                        "UPDATE chat_runs SET elapsed_seconds=elapsed_seconds+"
                        "TIMESTAMPDIFF(MICROSECOND,updated_at,UTC_TIMESTAMP(6))/1000000,"
                        "status='waiting_answer',updated_at=UTC_TIMESTAMP(6) "
                        "WHERE run_id=%s",
                        (run_id,),
                    )
                    await append_event(run_id, "agent_questionnaire", pending, job)
                return
            result = coordinator.compose_result(
                request.question,
                output,
                initial["start_time"] and datetime.fromisoformat(initial["start_time"]),
                run["session_id"],
                trace_id=run_id,
            )
            result = await _verify_final_result(request.question, result)
            async with transaction() as conn:
                await assert_lease(conn, job)
                rows = (
                    await conn.execute(
                        "SELECT event,data FROM run_events WHERE run_id=%s ORDER BY seq",
                        (run_id,),
                    )
                ).fetchall()
                events = [
                    {"event": row["event"], "data": decode(row)["data"]} for row in rows
                ]
                saved = await _persist_session_turn(
                    run["session_id"], request, result, events
                )
                result.update(saved)
                await conn.execute(
                    "UPDATE chat_runs SET elapsed_seconds=elapsed_seconds+"
                    "TIMESTAMPDIFF(MICROSECOND,updated_at,UTC_TIMESTAMP(6))/1000000,"
                    "status='completed',result=%s,updated_at=UTC_TIMESTAMP(6) "
                    "WHERE run_id=%s",
                    (json.dumps(result, default=str), run_id),
                )
                await coordinator._flush_trace(
                    trace_collector,
                    run_id,
                    run["session_id"],
                    "",
                    result.get("mode"),
                    result,
                )
                await enqueue("memory", f"memory:{run_id}", {"run_id": run_id})
                await append_event(run_id, "done", result, job)
    finally:
        execution_identity.reset(identity_token)
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        async with transaction() as conn:
            await conn.execute(
                "UPDATE chat_runs SET elapsed_seconds=elapsed_seconds+"
                "TIMESTAMPDIFF(MICROSECOND,updated_at,UTC_TIMESTAMP(6))/1000000,"
                "updated_at=UTC_TIMESTAMP(6) WHERE run_id=%s AND status='running' "
                "AND EXISTS (SELECT 1 FROM jobs WHERE job_id=%s AND token=%s "
                "AND owner=%s AND status='running' AND lease_until>UTC_TIMESTAMP(6))",
                (run_id, job["job_id"], job["token"], job["owner"]),
            )


def _event_name(name: str) -> str:
    return {
        "swarm_started": "agent_start",
        "subtask_started": "agent_start",
        "subtask_completed": "agent_complete",
        "swarm_completed": "agent_complete",
        "context_updated": "agent_tool_result",
        "agent_question": "agent_tool_call",
        "agent_answer": "agent_tool_result",
    }.get(name, name)
