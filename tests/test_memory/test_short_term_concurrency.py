"""短期记忆 per-session 锁的并发安全测试"""
import asyncio
from datetime import timedelta

import pytest

from mediZJ.memory.short_term import ShortTermMemory


@pytest.fixture
def memory():
    """隔离的 ShortTermMemory 实例（关闭熵管理以避免加载模型）"""
    ShortTermMemory._instance = None
    stm = ShortTermMemory(storage_type="memory")
    stm.entropy_manager = None
    yield stm
    ShortTermMemory._instance = None


async def test_concurrent_add_message_same_session(memory):
    """同会话并发写入不丢消息：2 协程 × 20 条 = 40 条"""
    session_id = "s-concurrent"

    async def writer(prefix: str):
        for i in range(20):
            await memory.add_message(session_id, "user", f"{prefix}-{i}")
            # 主动让出事件循环，制造交错机会
            await asyncio.sleep(0)

    await asyncio.gather(writer("a"), writer("b"))

    messages = memory.get_all_messages(session_id)
    assert len(messages) == 40
    contents = {m["content"] for m in messages}
    assert contents == {f"a-{i}" for i in range(20)} | {f"b-{i}" for i in range(20)}


async def test_concurrent_add_message_different_sessions(memory):
    """不同会话并发写入互不影响"""
    async def writer(session_id: str):
        for i in range(10):
            await memory.add_message(session_id, "user", f"{session_id}-{i}")
            await asyncio.sleep(0)

    await asyncio.gather(*[writer(f"s-{n}") for n in range(5)])

    for n in range(5):
        messages = memory.get_all_messages(f"s-{n}")
        assert len(messages) == 10
        assert all(m["content"].startswith(f"s-{n}-") for m in messages)


async def test_session_lock_reused_and_cleaned(memory):
    """同会话返回同一把锁；clear_session 后锁被回收"""
    lock1 = memory._get_session_lock("s-lock")
    lock2 = memory._get_session_lock("s-lock")
    assert lock1 is lock2

    await memory.add_message("s-lock", "user", "hello")
    memory.clear_session("s-lock")
    assert "s-lock" not in memory._session_locks


async def test_expired_session_evicted_with_lock(memory):
    """过期会话被 get_session 惰性清除时，其写锁一并回收"""
    memory.ttl_seconds = 1
    await memory.add_message("s-old", "user", "hello")
    assert "s-old" in memory._session_locks

    # 手动把 last_updated 拨到过去，使其过期
    memory.sessions["s-old"].last_updated -= timedelta(seconds=10)

    assert memory.get_session("s-old") is None
    assert "s-old" not in memory.sessions
    assert "s-old" not in memory._session_locks


async def test_add_message_triggers_full_eviction(memory):
    """add_message 周期性触发全量过期清理：过期会话连同锁一起移除"""
    memory.ttl_seconds = 1
    await memory.add_message("s-old-1", "user", "a")
    await memory.add_message("s-old-2", "user", "b")
    for sid in ("s-old-1", "s-old-2"):
        memory.sessions[sid].last_updated -= timedelta(seconds=10)
    # 重置节流计时器，确保下一次写入触发全量清理
    memory._last_evict_at = 0.0

    await memory.add_message("s-new", "user", "c")

    assert "s-old-1" not in memory.sessions
    assert "s-old-2" not in memory.sessions
    assert "s-old-1" not in memory._session_locks
    assert "s-old-2" not in memory._session_locks
    assert memory.get_session("s-new") is not None
