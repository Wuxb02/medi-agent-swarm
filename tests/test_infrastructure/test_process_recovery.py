"""两个独立应用进程中的断线、强制退出、问卷恢复与容量验收。"""

import asyncio
import json
import os
import socket
import sys
import uuid
from pathlib import Path

import httpx
import pytest

from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.settings import get_settings
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


async def eventually(callback, timeout=30):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        result = await callback()
        if result:
            return result
        await asyncio.sleep(0.1)
    raise AssertionError("状态未在期限内到达")


async def launch(tmp_path, delay="0", crash="0"):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **os.environ,
        "TEST_NODE_DELAY": delay,
        "TEST_CRASH_ON_INTERRUPT": crash,
        "UPLOAD_DIR": str(tmp_path),
        "RUN_CONCURRENCY": "1",
        "RUN_QUEUE_LIMIT": "1",
        "HEARTBEAT_SECONDS": "1",
        "LEASE_SECONDS": "30",
        "BACKGROUND_LLM_LIMIT": "1",
    }
    for key in (
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "http_proxy",
        "https_proxy",
    ):
        environment.pop(key, None)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("worker_process.py")),
        str(port),
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=None,
    )
    client = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False
    )

    async def ready():
        if process.returncode is not None:
            raise AssertionError(f"子进程退出: {process.returncode}")
        try:
            return (await client.get("/health/ready")).status_code == 200
        except httpx.TransportError:
            return False

    try:
        await eventually(ready, 60)
    except BaseException:
        await stop(process, client)
        raise
    return process, client


async def stop(process, client):
    if process.returncode is None:
        process.kill()
        await process.wait()
    await client.aclose()


async def expire_leases():
    async with transaction() as conn:
        await conn.execute(
            "UPDATE jobs SET lease_until=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) "
            "WHERE status='running'"
        )


async def setup_saver():
    async with AsyncMySaver.from_conn_string(get_settings().mysql_url) as saver:
        await saver.setup()


async def test_killed_process_resumes_and_events_replay_once(
    mysql_infrastructure, tmp_path
):
    await setup_saver()
    first, a = await launch(tmp_path, delay="60")
    second = b = None
    try:
        login = await a.post("/api/auth/login", json={"username": "patient"})
        assert login.status_code == 200
        created = await a.post("/api/chat/runs", json={"question": uuid.uuid4().hex})
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        async def started():
            run = (await a.get(f"/api/chat/runs/{run_id}")).json()
            return run if run["seq"] > 0 else None

        await eventually(started)
        cookies = dict(a.cookies)
        first.kill()
        await first.wait()
        await expire_leases()
        second, b = await launch(tmp_path)
        b.cookies.update(cookies)

        async def completed():
            run = (await b.get(f"/api/chat/runs/{run_id}")).json()
            return run if run["status"] == "completed" else None

        run = await eventually(completed)
        events = [
            json.loads(line)
            for line in (
                await b.get(f"/api/chat/runs/{run_id}/events?after=0")
            ).text.splitlines()
        ]
        assert events[-1]["event"] == "done"
        sequences = [event["seq"] for event in events]
        assert sequences == list(range(1, run["seq"] + 1))
        assert (
            await b.get(f"/api/chat/runs/{run_id}/events?after={run['seq']}")
        ).text == ""
        async with transaction() as conn:
            messages = (
                await conn.execute(
                    "SELECT COUNT(*) AS count FROM messages WHERE session_id=%s",
                    (run["session_id"],),
                )
            ).fetchone()
        assert messages["count"] == 2
    finally:
        await stop(first, a)
        if second:
            await stop(second, b)


async def test_interrupt_commit_crash_repairs_questionnaire_on_other_instance(
    mysql_infrastructure,
    tmp_path,
):
    await setup_saver()
    first, a = await launch(tmp_path, crash="1")
    second = b = None
    try:
        await a.post("/api/auth/login", json={"username": "patient"})
        cookies = dict(a.cookies)
        created = await a.post(
            "/api/chat/runs",
            json={
                "question": uuid.uuid4().hex,
                "context": {"questionnaire": True},
            },
        )
        run_id = created.json()["run_id"]
        await asyncio.wait_for(first.wait(), 30)
        assert first.returncode == 77
        await expire_leases()
        second, b = await launch(tmp_path)
        b.cookies.update(cookies)

        async def waiting():
            run = (await b.get(f"/api/chat/runs/{run_id}")).json()
            return run if run["status"] == "waiting_answer" else None

        run = await eventually(waiting)
        answers = {
            "run_id": run_id,
            "session_id": run["session_id"],
            "questionnaire_id": run["questionnaire"]["questionnaire_id"],
            "answers": {"q0": "同意"},
        }
        responses = await asyncio.gather(
            *[b.post("/api/chat/answer", json=answers) for _ in range(2)]
        )
        assert all(response.status_code == 200 for response in responses)
        assert (
            await b.post(
                "/api/chat/answer",
                json={
                    **answers,
                    "answers": {"q0": "不同"},
                },
            )
        ).status_code == 409

        async def completed():
            run = (await b.get(f"/api/chat/runs/{run_id}")).json()
            return run if run["status"] == "completed" else None

        result = await eventually(completed)
        assert "同意" in result["result"]["answer"]
    finally:
        await stop(first, a)
        if second:
            await stop(second, b)


async def test_two_live_instances_share_capacity_and_cross_node_cancel(
    mysql_infrastructure,
    tmp_path,
):
    await setup_saver()
    first, a = await launch(tmp_path, delay="60")
    second, b = await launch(tmp_path, delay="60")
    try:
        await a.post("/api/auth/login", json={"username": "patient"})
        b.cookies.update(dict(a.cookies))
        created = (
            await a.post("/api/chat/runs", json={"question": uuid.uuid4().hex})
        ).json()
        run_id = created["run_id"]

        async def started():
            return (await b.get(f"/api/chat/runs/{run_id}")).json()["seq"] > 0

        await eventually(started)
        queued = await b.post("/api/chat/runs", json={"question": uuid.uuid4().hex})
        assert queued.status_code == 202
        rejected = await a.post("/api/chat/runs", json={"question": uuid.uuid4().hex})
        assert rejected.status_code == 429 and rejected.headers["Retry-After"]
        await a.post("/api/auth/login", json={"username": "another"})
        rejected = await a.post("/api/chat/runs", json={"question": uuid.uuid4().hex})
        assert rejected.status_code == 503 and rejected.headers["Retry-After"]
        async with transaction() as conn:
            active = (
                await conn.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE "
                    "kind='chat' AND status='running' AND "
                    "lease_until>UTC_TIMESTAMP(6)"
                )
            ).fetchone()
        assert active["count"] == 1
        cancelled = await b.post(f"/api/chat/runs/{run_id}/cancel")
        assert (
            cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        )
        assert (await b.post(f"/api/chat/runs/{run_id}/cancel")).status_code == 200
        assert (
            json.loads(
                (await b.get(f"/api/chat/runs/{run_id}/events")).text.splitlines()[-1]
            )["event"]
            == "error"
        )
        await b.post(f"/api/chat/runs/{queued.json()['run_id']}/cancel")
    finally:
        await stop(first, a)
        await stop(second, b)
