"""真实 Redis 多实例 CAS、TTL 与连接故障测试。"""

import asyncio
from unittest.mock import patch

import pytest
from redis.exceptions import ConnectionError
from mediZJ.memory.short_term import ShortTermMemory
from mediZJ.infrastructure.redis_client import get_redis

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


async def test_concurrent_instances_preserve_messages(mysql_infrastructure):
    async def writer(prefix):
        memory = ShortTermMemory("alice")
        for index in range(20):
            await memory.add_message("s", "user", f"{prefix}-{index}")

    await asyncio.gather(writer("a"), writer("b"))
    messages = await ShortTermMemory("alice").get_all_messages("s")
    assert {m["content"] for m in messages} == {
        f"{prefix}-{index}" for prefix in ("a", "b") for index in range(20)
    }


async def test_old_revision_and_watermark_cannot_overwrite(mysql_infrastructure):
    memory = ShortTermMemory("alice")
    await memory.restore_session("s", [{"id": 2, "role": "user", "content": "新"}])
    history, revision = await memory._read("s")
    await memory.add_message("s", "assistant", "临时执行状态")
    assert not await memory._write(history, revision)
    assert not await memory.restore_session(
        "s", [{"id": 1, "role": "user", "content": "旧"}]
    )
    assert len(await memory.get_all_messages("s")) == 2


async def test_ttl_slides_and_expired_cache_rebuilds(mysql_infrastructure):
    memory = ShortTermMemory("alice")
    memory.settings = memory.settings.model_copy(update={"short_term_ttl": 2})
    messages = [{"id": 3, "role": "user", "content": "历史"}]
    await memory.restore_session("s", messages)
    client = get_redis()
    await client.expire(memory._key("s"), 1)
    await memory.get_session("s")
    assert await client.ttl(memory._key("s")) == 2
    await client.pexpire(memory._key("s"), 1)
    await asyncio.sleep(0.02)
    assert await memory.get_session("s") is None
    assert await memory.restore_session("s", messages)
    assert await memory.get_all_messages("s") == messages


async def test_connection_failure_is_reported(mysql_infrastructure):
    memory = ShortTermMemory("alice")
    with patch("mediZJ.memory.short_term.get_redis", side_effect=ConnectionError):
        with pytest.raises(ConnectionError):
            await memory.get_session("s")
    assert not hasattr(memory, "sessions")
