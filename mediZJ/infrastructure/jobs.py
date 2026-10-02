"""MySQL 持久化任务、租约与幂等执行。"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from loguru import logger

from .database import transaction
from .settings import get_settings
from .events import terminate
from .metrics import increment

job_context: ContextVar[dict | None] = ContextVar("job_context", default=None)


class LeaseLost(RuntimeError):
    """执行已由其他工作器接管。"""


def decode(row: Any) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("payload", "request", "result", "data", "answers"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result


async def enqueue(kind: str, key: str, payload: dict) -> str:
    async with transaction() as conn:
        job_id = uuid.uuid4().hex
        await conn.execute(
            "INSERT INTO jobs (job_id,kind,dedup_key,payload,status,scheduled_at,created_at) "
            "VALUES (%s,%s,%s,%s,'pending',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6)) "
            "ON DUPLICATE KEY UPDATE dedup_key=dedup_key",
            (job_id, kind, key, json.dumps(payload, ensure_ascii=False)),
        )
        row = (
            await conn.execute(
                "SELECT job_id FROM jobs WHERE dedup_key=%s",
                (key,),
            )
        ).fetchone()
        return row["job_id"]


async def assert_lease(conn, job: dict) -> None:
    row = (
        await conn.execute(
            "SELECT token FROM jobs WHERE job_id=%s AND status='running' "
            "AND token=%s AND owner=%s AND lease_until>UTC_TIMESTAMP(6) FOR UPDATE",
            (job["job_id"], job["token"], job["owner"]),
        )
    ).fetchone()
    if row is None:
        raise LeaseLost(job["job_id"])


async def claim(kind: str, owner: str) -> dict | None:
    settings = get_settings()
    async with transaction() as conn:
        await conn.execute("SELECT name FROM admission WHERE name='runs' FOR UPDATE")
        # 崩溃回收同样受次数上限约束。
        exhausted = (
            await conn.execute(
                "SELECT job_id,payload FROM jobs WHERE kind=%s AND attempts>=3 "
                "AND status='running' AND lease_until<=UTC_TIMESTAMP(6) FOR UPDATE",
                (kind,),
            )
        ).fetchall()
        for row in exhausted:
            await conn.execute(
                "UPDATE jobs SET status='failed',error='执行尝试次数耗尽',lease_until=NULL "
                "WHERE job_id=%s",
                (row["job_id"],),
            )
            if kind == "chat":
                decoded = decode(row)
                assert decoded is not None
                payload = decoded["payload"]
                await terminate(conn, payload["run_id"], "failed", "执行尝试次数耗尽")
            await increment("job_attempts_exhausted")
        if kind == "chat":
            # 终态事件持久化，重连时按序号补读。
            expired = (
                await conn.execute(
                    "SELECT run_id FROM chat_runs WHERE status='queued' AND "
                    "TIMESTAMPDIFF(SECOND,updated_at,UTC_TIMESTAMP(6))>%s FOR UPDATE",
                    (settings.run_queue_timeout,),
                )
            ).fetchall()
            for row in expired:
                await terminate(conn, row["run_id"], "expired", "排队等待超时")
            expired = (
                await conn.execute(
                    "SELECT DISTINCT r.run_id FROM chat_runs r JOIN questionnaires q "
                    "ON q.run_id=r.run_id WHERE r.status='waiting_answer' AND "
                    "q.status='pending' AND q.expires_at<=UTC_TIMESTAMP(6)"
                )
            ).fetchall()
            for row in expired:
                await terminate(conn, row["run_id"], "expired", "问卷已过期")
            await conn.execute(
                "UPDATE jobs j JOIN chat_runs r "
                "ON r.run_id=JSON_UNQUOTE(JSON_EXTRACT(j.payload,'$.run_id')) "
                "SET j.status='completed',j.lease_until=NULL "
                "WHERE j.kind='chat' AND j.status='running' "
                "AND j.lease_until<=UTC_TIMESTAMP(6) "
                "AND r.status IN ('completed','waiting_answer')"
            )
            await conn.execute(
                "UPDATE jobs j JOIN chat_runs r ON r.run_id=JSON_UNQUOTE(JSON_EXTRACT(j.payload,'$.run_id')) "
                "SET j.status='cancelled',j.token=j.token+1,j.lease_until=NULL "
                "WHERE j.kind='chat' AND j.status IN ('pending','running') "
                "AND r.status IN ('expired','cancelled')"
            )
            count = (
                await conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE kind='chat' "
                    "AND status='running' AND lease_until>UTC_TIMESTAMP(6)",
                )
            ).fetchone()["count"]
            if count >= settings.run_concurrency:
                return None
        row = (
            await conn.execute(
                "SELECT * FROM jobs WHERE kind=%s AND attempts<3 AND "
                "(kind!='chat' OR EXISTS (SELECT 1 FROM chat_runs r "
                "WHERE r.run_id=JSON_UNQUOTE(JSON_EXTRACT(jobs.payload,'$.run_id')) "
                "AND r.status IN ('queued','running'))) AND "
                "((status='pending' AND scheduled_at<=UTC_TIMESTAMP(6)) OR "
                "(status='running' AND lease_until<=UTC_TIMESTAMP(6))) "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED",
                (kind,),
            )
        ).fetchone()
        if row is None:
            return None
        if row["status"] == "running":
            await increment("lease_recovered")
        await conn.execute(
            "UPDATE jobs SET status='running',attempts=attempts+1,token=token+1,"
            "owner=%s,lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND) "
            "WHERE job_id=%s",
            (owner, settings.lease_seconds, row["job_id"]),
        )
        return decode(
            (
                await conn.execute(
                    "SELECT * FROM jobs WHERE job_id=%s",
                    (row["job_id"],),
                )
            ).fetchone()
        )


async def renew(job: dict) -> None:
    async with transaction() as conn:
        await assert_lease(conn, job)
        if job["kind"] == "chat":
            await conn.execute(
                "UPDATE chat_runs SET elapsed_seconds=elapsed_seconds+"
                "TIMESTAMPDIFF(MICROSECOND,updated_at,UTC_TIMESTAMP(6))/1000000,"
                "updated_at=UTC_TIMESTAMP(6) WHERE run_id=%s AND status='running'",
                (job["payload"]["run_id"],),
            )
        await conn.execute(
            "UPDATE jobs SET lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND) "
            "WHERE job_id=%s",
            (get_settings().lease_seconds, job["job_id"]),
        )


async def finish(job: dict) -> None:
    async with transaction() as conn:
        await assert_lease(conn, job)
        await conn.execute(
            "UPDATE jobs SET status='completed',lease_until=NULL WHERE job_id=%s",
            (job["job_id"],),
        )
        if job["kind"] in {"cache_delete", "session_delete"}:
            from .handlers import complete_cleanup

            await complete_cleanup(job)


async def fail(job: dict, error: Exception) -> None:
    async with transaction() as conn:
        await assert_lease(conn, job)
        terminal = job["attempts"] >= 3
        await conn.execute(
            "UPDATE jobs SET status=%s,error=%s,lease_until=NULL,"
            "scheduled_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND) WHERE job_id=%s",
            (
                "failed" if terminal else "pending",
                type(error).__name__,
                5 if job["attempts"] == 1 else 30,
                job["job_id"],
            ),
        )
        if terminal and job["kind"] == "chat":
            await terminate(
                conn, job["payload"]["run_id"], "failed", type(error).__name__
            )
        if job["kind"] == "evaluation":
            await conn.execute(
                "UPDATE evaluation_jobs SET status=%s,last_error=%s,updated_at=%s "
                "WHERE job_id=%s AND status NOT IN ('completed','superseded')",
                (
                    "failed" if terminal else "pending",
                    type(error).__name__,
                    datetime.now(timezone.utc).isoformat(),
                    job["payload"]["evaluation_job_id"],
                ),
            )
        await increment("job_failed" if terminal else "job_retried")
        if terminal and job["kind"] == "knowledge_index":
            await conn.execute(
                "UPDATE knowledge_documents SET status='failed',error=%s "
                "WHERE version_id=%s AND status='indexing'",
                (type(error).__name__, job["payload"]["version_id"]),
            )


class JobWorker:
    """受监护的有界工作器；每个槽位一次只领取一个任务。"""

    def __init__(
        self, handlers: dict[str, Callable[[dict], Awaitable]], concurrency: int = 4
    ):
        self.handlers = handlers
        self.concurrency = concurrency
        self.owner = uuid.uuid4().hex
        self.tasks: list[asyncio.Task] = []
        self.stop_event = asyncio.Event()
        self.last_heartbeat = 0.0

    async def start(self):
        self.tasks = [
            asyncio.create_task(self._loop()) for _ in range(self.concurrency)
        ]

    async def stop(self):
        self.stop_event.set()
        try:
            await asyncio.wait_for(asyncio.gather(*self.tasks), timeout=30)
        except asyncio.TimeoutError:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _heartbeat(self, job):
        while True:
            await asyncio.sleep(get_settings().heartbeat_seconds)
            await renew(job)

    async def _execute(self, job):
        with logger.contextualize(
            job_id=job["job_id"], run_id=job["payload"].get("run_id", "-")
        ):
            await self._execute_owned(job)

    async def _execute_owned(self, job):
        token = job_context.set(job)
        work = asyncio.create_task(self.handlers[job["kind"]](job))
        heartbeat = asyncio.create_task(self._heartbeat(job))
        try:
            done, _ = await asyncio.wait(
                (work, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                await heartbeat
                raise LeaseLost(job["job_id"])
            await work
            await finish(job)
        except LeaseLost:
            logger.warning("任务租约失效: job={}", job["job_id"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "任务失败: job={} kind={} error={}",
                job["job_id"],
                job["kind"],
                type(exc).__name__,
            )
            await fail(job, exc)
        finally:
            work.cancel()
            heartbeat.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)
            job_context.reset(token)

    async def _loop(self):
        while not self.stop_event.is_set():
            self.last_heartbeat = asyncio.get_running_loop().time()
            try:
                found = False
                for kind in self.handlers:
                    job = await claim(kind, self.owner)
                    if job:
                        found = True
                        await self._execute(job)
                if not found:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("工作器异常，将重新轮询: owner={}", self.owner)
                await asyncio.sleep(1)
