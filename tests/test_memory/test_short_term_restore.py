"""test_memory/test_short_term_restore.py — 会话历史恢复回填测试

覆盖：SessionDB.get_recent_turns 轻量查询 + ShortTermMemory.restore_session 回填。
"""
import asyncio
from datetime import datetime

import pytest

from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.short_term import ShortTermMemory


@pytest.fixture
def db(tmp_path):
    """每个用例使用独立的临时数据库"""
    SessionDB.reset()
    instance = SessionDB(str(tmp_path / "sessions.db"))
    yield instance
    SessionDB.reset()


@pytest.fixture
def stm():
    """隔离的 ShortTermMemory 实例（关闭熵管理以避免加载模型）"""
    ShortTermMemory._instance = None
    instance = ShortTermMemory(storage_type="memory")
    instance.entropy_manager = None
    yield instance
    ShortTermMemory._instance = None


def _save_turn(db, session_id, turn_index, user_text, assistant_text,
               user_id="default"):
    """写入一轮对话（user + assistant）"""
    now = datetime.now().isoformat()
    db.save_turn(
        session_id=session_id,
        turn_index=turn_index,
        user_msg={"role": "user", "content": user_text, "timestamp": now},
        assistant_msg={
            "role": "assistant", "content": assistant_text, "timestamp": now,
        },
        user_id=user_id,
    )


class TestGetRecentTurns:
    def test_returns_recent_turns_in_order(self, db):
        """10 轮会话取 limit=10 返回 20 条，时间正序（旧→新）"""
        _save_turn(db, "s1", 0, "问0", "答0")
        _save_turn(db, "s1", 1, "问1", "答1")
        _save_turn(db, "s1", 2, "问2", "答2")

        messages = db.get_recent_turns("s1", limit=10)
        assert len(messages) == 6
        assert [m["role"] for m in messages] == [
            "user", "assistant", "user", "assistant", "user", "assistant"
        ]
        assert messages[0]["content"] == "问0"
        assert messages[-1]["content"] == "答2"

    def test_limit_none_returns_all(self, db):
        """limit=None 返回全部消息"""
        for i in range(12):
            _save_turn(db, "s-all", i, f"问{i}", f"答{i}")

        messages = db.get_recent_turns("s-all", limit=None)
        assert len(messages) == 24
        assert messages[0]["content"] == "问0"
        assert messages[-1]["content"] == "答11"

    def test_limit_half_turns(self, db):
        """limit=3 只返回最近 3 轮（6 条）"""
        for i in range(10):
            _save_turn(db, "s2", i, f"问{i}", f"答{i}")

        messages = db.get_recent_turns("s2", limit=3)
        assert len(messages) == 6
        assert messages[0]["content"] == "问7"
        assert messages[-1]["content"] == "答9"

    def test_unknown_session_returns_empty(self, db):
        assert db.get_recent_turns("no-such", limit=10) == []

    def test_user_id_filtered(self, db):
        """不属于 user_id 的会话返回空"""
        _save_turn(db, "s3", 0, "问0", "答0", user_id="alice")
        assert db.get_recent_turns("s3", user_id="bob", limit=10) == []
        assert len(db.get_recent_turns("s3", user_id="alice", limit=10)) == 2


class TestRestoreSession:
    @pytest.mark.asyncio
    async def test_restore_then_read_back(self, db, stm):
        """回填后 get_recent_messages 返回正序消息"""
        _save_turn(db, "s1", 0, "问0", "答0")
        _save_turn(db, "s1", 1, "问1", "答1")

        messages = db.get_recent_turns("s1", limit=10)
        restored = await stm.restore_session("s1", messages)
        assert restored is True

        recent = await stm.get_recent_messages("s1", limit=10)
        assert len(recent) == 4
        assert recent[0]["role"] == "user"
        assert recent[0]["content"] == "问0"
        assert recent[-1]["content"] == "答1"

    @pytest.mark.asyncio
    async def test_get_recent_messages_limit_none(self, db, stm):
        """limit=None 返回全部消息"""
        _save_turn(db, "s1", 0, "问0", "答0")
        _save_turn(db, "s1", 1, "问1", "答1")

        messages = db.get_recent_turns("s1", limit=None)
        await stm.restore_session("s1", messages)

        all_msgs = await stm.get_recent_messages("s1", limit=None)
        assert len(all_msgs) == 4

    @pytest.mark.asyncio
    async def test_restore_low_entropy_keeps_full(self, db, stm):
        """低熵历史回填后保持完整（不压缩），全量上下文可用"""
        from types import SimpleNamespace

        _save_turn(db, "s1", 0, "问0", "答0")
        _save_turn(db, "s1", 1, "问1", "答1")
        messages = db.get_recent_turns("s1", limit=None)

        compressed = []

        async def _fake_compress(*args, **kwargs):
            compressed.append(True)
            return []

        stm.entropy_manager = SimpleNamespace(
            estimate_entropy=lambda *a, **k: {
                "entropy_level": "low", "total_messages": 4,
                "duplicate_rate": 0.0,
            },
            deduplicate_messages=lambda m, **k: m,
            _compress_older_messages=_fake_compress,
        )

        restored = await stm.restore_session("s1", messages)
        assert restored is True
        assert compressed == [], "低熵回填不应触发压缩"

        all_msgs = await stm.get_recent_messages("s1", limit=None)
        assert len(all_msgs) == 4  # 完整保留

    @pytest.mark.asyncio
    async def test_restore_high_entropy_compresses(self, db, stm):
        """高熵历史回填后触发现有压缩策略（_compress_older_messages 被调用）"""
        from types import SimpleNamespace

        # 12 轮（24 条）：保证超过 keep_recent=5，可压缩区间非空
        for i in range(12):
            _save_turn(db, "s1", i, f"问{i}", f"答{i}")
        messages = db.get_recent_turns("s1", limit=None)

        compressed = []

        async def _fake_compress(*args, **kwargs):
            compressed.append(True)
            return [{"role": "system", "content": "[摘要] 旧消息"}]

        stm.entropy_manager = SimpleNamespace(
            estimate_entropy=lambda *a, **k: {
                "entropy_level": "high", "total_messages": 24,
                "duplicate_rate": 0.0,
            },
            deduplicate_messages=lambda m, **k: m,
            _compress_older_messages=_fake_compress,
        )

        restored = await stm.restore_session("s1", messages)
        assert restored is True
        assert compressed, "高熵回填应触发压缩"

    @pytest.mark.asyncio
    async def test_restore_is_idempotent(self, db, stm):
        """已有消息的会话再次回填返回 False 且不覆盖"""
        _save_turn(db, "s1", 0, "问0", "答0")
        await stm.add_message("s1", "user", "新消息")

        messages = db.get_recent_turns("s1", limit=10)
        restored = await stm.restore_session("s1", messages)
        assert restored is False

        recent = await stm.get_recent_messages("s1", limit=10)
        assert recent[0]["content"] == "新消息"  # 未被 SQLite 历史覆盖

    @pytest.mark.asyncio
    async def test_restore_empty_returns_false(self, stm):
        restored = await stm.restore_session("s-empty", [])
        assert restored is False

    @pytest.mark.asyncio
    async def test_restore_refreshes_last_updated(self, db, stm):
        """回填后 last_updated 刷新，TTL 重新计时"""
        _save_turn(db, "s1", 0, "问0", "答0")
        messages = db.get_recent_turns("s1", limit=10)

        stm.ttl_seconds = 1
        await stm.restore_session("s1", messages)
        assert stm.get_session("s1") is not None  # 未过期

    @pytest.mark.asyncio
    async def test_concurrent_restore_and_add_do_not_lose_messages(self, db, stm):
        """并发 restore + add_message 不丢消息（per-session 锁互斥）"""
        _save_turn(db, "s1", 0, "问0", "答0")
        messages = db.get_recent_turns("s1", limit=10)

        async def writer():
            for i in range(10):
                await stm.add_message("s1", "user", f"new-{i}")
                await asyncio.sleep(0)

        async def restorer():
            await stm.restore_session("s1", messages)
            await asyncio.sleep(0)

        await asyncio.gather(restorer(), writer())

        all_msgs = stm.get_all_messages("s1")
        contents = {m["content"] for m in all_msgs}
        # restore 要么成功（历史 + new-* 都存在），要么幂等跳过（仅 new-*）
        assert {"问0", "答0"} <= contents or "问0" not in contents
        assert {f"new-{i}" for i in range(10)} <= contents
