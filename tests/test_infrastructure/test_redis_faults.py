"""独立 Redis 容器重启、真实淘汰及 MySQL 历史重建。"""

import asyncio
import os
import subprocess
import socket
import uuid

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from mediZJ.infrastructure.redis_client import close_redis, initialize_redis
from mediZJ.infrastructure.settings import get_settings
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.short_term import ShortTermMemory

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


def docker(*args):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.skipif(
    os.environ.get("TEST_DOCKER") != "1", reason="需显式启用独立容器故障测试"
)
async def test_restart_and_eviction_rebuild_from_mysql(
    mysql_infrastructure, monkeypatch
):
    name = "medizj-redis-fault-" + uuid.uuid4().hex[:8]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        host_port = listener.getsockname()[1]
    await asyncio.to_thread(
        docker,
        "run",
        "-d",
        "--name",
        name,
        "-p",
        f"127.0.0.1:{host_port}:6379",
        "redis:7.4.2",
        "redis-server",
        "--appendonly",
        "yes",
        "--maxmemory",
        "512mb",
        "--maxmemory-policy",
        "allkeys-lru",
    )
    probe = None
    try:
        port = await asyncio.to_thread(docker, "port", name, "6379/tcp")
        url = f"redis://{port}/0"
        probe = Redis.from_url(url, decode_responses=True)

        async def ready():
            for _ in range(100):
                try:
                    await probe.ping()
                    return
                except ConnectionError:
                    await asyncio.sleep(0.1)
            raise AssertionError("Redis 未就绪")

        await ready()
        await close_redis()
        monkeypatch.setenv("REDIS_URL", url)
        get_settings.cache_clear()
        await initialize_redis()
        db = SessionDB()
        owner = (await db.get_or_create_user("fault-user"))["user_id"]
        await db.save_turn(
            "fault-session", 0, {"content": "确认历史"}, {"content": "确认答案"}, owner
        )
        history = await db.get_recent_turns("fault-session", owner, None)
        memory = ShortTermMemory(owner)
        assert await memory.restore_session("fault-session", history)
        await asyncio.to_thread(docker, "restart", name)
        await ready()
        assert (await memory.get_session("fault-session")).messages == history
        used = (await probe.info("memory"))["used_memory"]
        await probe.config_set("maxmemory", used + 4 * 1024 * 1024)
        await asyncio.sleep(1.1)
        for index in range(500):
            await probe.set(f"pressure:{index}", "x" * 65536)
        assert (await probe.info("stats"))["evicted_keys"] > 0
        assert not await probe.exists(memory._key("fault-session"))
        await probe.config_set("maxmemory", 512 * 1024 * 1024)
        assert await memory.restore_session("fault-session", history)
        assert (await memory.get_session("fault-session")).messages == history
    finally:
        await close_redis()
        if probe:
            await probe.aclose()
        await asyncio.to_thread(docker, "rm", "-f", "-v", name)
