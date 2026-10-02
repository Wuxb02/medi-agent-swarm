"""真实数据库中的全集群准入、容量租约和终态恢复。"""

import asyncio

import pytest
from fastapi import HTTPException

from mediZJ.api.models.chat import ChatRequest
from mediZJ.api.services.run_service import cancel_run, create_run, get_run, read_events
from mediZJ.infrastructure.capacity import (
    CapacityExceeded,
    acquire,
    llm_capacity,
    release,
)
from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.jobs import claim, enqueue, fail, renew
from mediZJ.infrastructure.metrics import snapshot
from mediZJ.infrastructure.settings import get_settings
from mediZJ.memory.session_db import SessionDB

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


async def user(name):
    return (await SessionDB().get_or_create_user(name))["user_id"]


async def test_admission_is_cluster_wide(mysql_infrastructure):
    settings = get_settings()
    settings.run_queue_limit = 2
    settings.user_run_limit = 1
    alice, bob, carol = await asyncio.gather(user("alice"), user("bob"), user("carol"))
    first = await create_run(ChatRequest(question="一"), alice, "first")
    with pytest.raises(HTTPException) as error:
        await create_run(ChatRequest(question="二"), alice, None)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"]
    await create_run(ChatRequest(question="三"), bob, None)
    with pytest.raises(HTTPException) as error:
        await create_run(ChatRequest(question="四"), carol, None)
    assert error.value.status_code == 503
    settings.run_concurrency = 1
    job = await claim("chat", "a")
    assert await claim("chat", "b") is None
    await renew(job)
    await cancel_run(first["run_id"], alice)
    assert await claim("chat", "b") is not None
    metrics = await snapshot()
    assert metrics["counters"]["run_rejected_429"] == 1
    assert metrics["counters"]["run_rejected_503"] == 1


async def test_expiry_and_crash_exhaustion_emit_durable_events(mysql_infrastructure):
    owner = await user("alice")
    expired = await create_run(ChatRequest(question="排队"), owner, None)
    async with transaction() as conn:
        await conn.execute(
            "UPDATE chat_runs SET updated_at=DATE_SUB(UTC_TIMESTAMP(6),"
            "INTERVAL 31 SECOND) WHERE run_id=%s",
            (expired["run_id"],),
        )
    assert await claim("chat", "worker") is None
    assert (await get_run(expired["run_id"], owner))["status"] == "expired"
    assert (await read_events(expired["run_id"], owner, 0))[0]["seq"] == 1
    run = await create_run(ChatRequest(question="崩溃"), owner, None)
    for expected in range(1, 4):
        job = await claim("chat", f"worker-{expected}")
        assert job["attempts"] == expected
        async with transaction() as conn:
            await conn.execute(
                "UPDATE jobs SET lease_until=DATE_SUB(UTC_TIMESTAMP(6),"
                "INTERVAL 1 SECOND) WHERE job_id=%s",
                (job["job_id"],),
            )
    assert await claim("chat", "fourth") is None
    assert (await get_run(run["run_id"], owner))["status"] == "failed"
    events = await read_events(run["run_id"], owner, 0)
    assert len(events) == 1 and events[0]["event"] == "error"
    assert (await snapshot())["counters"]["lease_recovered"] == 2


async def test_llm_slots_fence_old_release_and_background_limit(mysql_infrastructure):
    get_settings().background_llm_limit = 1
    first = await acquire("llm", "first", background=True)
    assert await acquire("llm", "second", background=True) is None
    async with transaction() as conn:
        await conn.execute(
            "UPDATE capacity_slots SET lease_until=DATE_SUB(UTC_TIMESTAMP(6),"
            "INTERVAL 1 SECOND) WHERE slot_id=%s",
            (first[0],),
        )
    next_owner = await acquire("llm", "next", background=True)
    await release(first)
    async with transaction() as conn:
        assert (
            await conn.execute(
                "SELECT owner FROM capacity_slots WHERE slot_id=%s", (next_owner[0],)
            )
        ).fetchone()["owner"] == "next"
    await release(next_owner)
    await release(None)
    async with llm_capacity():
        assert (await snapshot())["capacity"] == [{"resource": "llm", "active": 1}]
    assert (await snapshot())["capacity"] == []


async def test_llm_queue_refusal_timeout_and_job_failure_retry(mysql_infrastructure):
    async with transaction() as conn:
        await conn.execute(
            "UPDATE capacity_slots SET owner='busy',lease_until="
            "DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 60 SECOND)"
        )
    with pytest.raises(CapacityExceeded, match="已满"):
        async with llm_capacity():
            pytest.fail("不应进入模型调用")
    async with transaction() as conn:
        await conn.execute(
            "UPDATE capacity_slots SET owner=NULL,lease_until=NULL "
            "WHERE resource='llm_wait'"
        )
    get_settings().llm_queue_timeout = 1
    with pytest.raises(CapacityExceeded, match="超时"):
        async with llm_capacity():
            pytest.fail("不应进入模型调用")
    await enqueue("test", "same", {})
    for attempt in range(1, 4):
        job = await claim("test", "worker")
        assert job["attempts"] == attempt
        await fail(job, RuntimeError("不应持久化患者原文"))
        assert await claim("test", "worker") is None
        async with transaction() as conn:
            row = (
                await conn.execute(
                    "SELECT status,error,TIMESTAMPDIFF(SECOND,"
                    "UTC_TIMESTAMP(6),scheduled_at) AS delay FROM jobs "
                    "WHERE job_id=%s",
                    (job["job_id"],),
                )
            ).fetchone()
            assert row["error"] == "RuntimeError"
            assert row["status"] == ("failed" if attempt == 3 else "pending")
            await conn.execute(
                "UPDATE jobs SET scheduled_at=UTC_TIMESTAMP(6) WHERE job_id=%s",
                (job["job_id"],),
            )
    assert await claim("test", "worker") is None
