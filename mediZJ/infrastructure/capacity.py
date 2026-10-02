"""数据库租约控制的全集群 LLM 容量。"""

import asyncio
import uuid
from contextlib import asynccontextmanager

from .database import transaction
from .jobs import LeaseLost, job_context
from .settings import get_settings


class CapacityExceeded(RuntimeError):
    """请求超出上游调用等待容量。"""


async def acquire(resource, owner, background=False):
    settings = get_settings()
    async with transaction() as conn:
        await conn.execute("SELECT name FROM admission WHERE name='llm' FOR UPDATE")
        if resource == "llm" and background:
            count = (
                await conn.execute(
                    "SELECT COUNT(*) AS count FROM capacity_slots WHERE resource='llm' "
                    "AND background=1 AND lease_until>UTC_TIMESTAMP(6)",
                )
            ).fetchone()["count"]
            if count >= settings.background_llm_limit:
                return None
        row = (
            await conn.execute(
                "SELECT slot_id,token FROM capacity_slots WHERE resource=%s "
                "AND (lease_until IS NULL OR lease_until<=UTC_TIMESTAMP(6)) "
                "ORDER BY slot_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (resource,),
            )
        ).fetchone()
        if row is None:
            return None
        await conn.execute(
            "UPDATE capacity_slots SET owner=%s,background=%s,token=token+1,"
            "lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND) WHERE slot_id=%s",
            (owner, int(background), settings.lease_seconds, row["slot_id"]),
        )
        return (row["slot_id"], row["token"] + 1, owner)


async def release(slot):
    if slot is None:
        return
    async with transaction() as conn:
        await conn.execute(
            "UPDATE capacity_slots SET owner=NULL,lease_until=NULL WHERE slot_id=%s "
            "AND token=%s AND owner=%s",
            slot,
        )


@asynccontextmanager
async def llm_capacity():
    settings = get_settings()
    owner = uuid.uuid4().hex
    context = job_context.get()
    background = bool(context and context["kind"] != "chat")
    waiting = await acquire("llm_wait", owner)
    if waiting is None:
        raise CapacityExceeded("LLM 等待队列已满")
    active = None
    heartbeat = None
    lease_lost = False
    parent = asyncio.current_task()

    async def renew():
        nonlocal lease_lost
        try:
            while True:
                await asyncio.sleep(settings.heartbeat_seconds)
                async with transaction() as conn:
                    result = await conn.execute(
                        "UPDATE capacity_slots SET lease_until=DATE_ADD(UTC_TIMESTAMP(6),"
                        "INTERVAL %s SECOND) WHERE slot_id=%s AND token=%s AND owner=%s "
                        "AND lease_until>UTC_TIMESTAMP(6)",
                        (settings.lease_seconds, *active),
                    )
                    if result.rowcount != 1:
                        raise LeaseLost("LLM 槽位失效")
        except asyncio.CancelledError:
            raise
        except Exception:
            lease_lost = True
            parent.cancel()

    try:
        deadline = asyncio.get_running_loop().time() + settings.llm_queue_timeout
        while active is None:
            active = await acquire("llm", owner, background)
            if active is None:
                if asyncio.get_running_loop().time() >= deadline:
                    raise CapacityExceeded("LLM 排队等待超时")
                await asyncio.sleep(0.1)
        await release(waiting)
        waiting = None
        heartbeat = asyncio.create_task(renew())
        yield
    except asyncio.CancelledError:
        if lease_lost:
            raise LeaseLost("LLM 容量租约已失效") from None
        raise
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await release(active)
        await release(waiting)
