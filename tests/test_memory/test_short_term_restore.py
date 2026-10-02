"""test_memory/test_short_term_restore.py — 会话历史恢复回填测试

覆盖：SessionDB.get_recent_turns 轻量查询 + ShortTermMemory.restore_session 回填。
"""

from datetime import datetime
import pytest

from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.short_term import ShortTermMemory

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


@pytest.fixture
def db(mysql_infrastructure):
    """每个用例使用独立的临时数据库"""
    SessionDB.reset()
    instance = SessionDB()
    yield instance
    SessionDB.reset()


@pytest.fixture
def stm(mysql_infrastructure):
    return ShortTermMemory()


async def _save_turn(
    db, session_id, turn_index, user_text, assistant_text, user_id="default"
):
    """写入一轮对话（user + assistant）"""
    now = datetime.now().isoformat()
    await db.save_turn(
        session_id=session_id,
        turn_index=turn_index,
        user_msg={"role": "user", "content": user_text, "timestamp": now},
        assistant_msg={
            "role": "assistant",
            "content": assistant_text,
            "timestamp": now,
        },
        user_id=user_id,
    )


class TestGetRecentTurns:
    async def test_returns_recent_turns_in_order(self, db):
        """10 轮会话取 limit=10 返回 20 条，时间正序（旧→新）"""
        await _save_turn(db, "s1", 0, "问0", "答0")
        await _save_turn(db, "s1", 1, "问1", "答1")
        await _save_turn(db, "s1", 2, "问2", "答2")
        messages = await db.get_recent_turns("s1", limit=10)
        assert len(messages) == 6
        assert [m["role"] for m in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert messages[0]["content"] == "问0"
        assert messages[-1]["content"] == "答2"

    async def test_limit_none_returns_all(self, db):
        """limit=None 返回全部消息"""
        for i in range(12):
            await _save_turn(db, "s-all", i, f"问{i}", f"答{i}")
        messages = await db.get_recent_turns("s-all", limit=None)
        assert len(messages) == 24
        assert messages[0]["content"] == "问0"
        assert messages[-1]["content"] == "答11"

    async def test_limit_half_turns(self, db):
        """limit=3 只返回最近 3 轮（6 条）"""
        for i in range(10):
            await _save_turn(db, "s2", i, f"问{i}", f"答{i}")
        messages = await db.get_recent_turns("s2", limit=3)
        assert len(messages) == 6
        assert messages[0]["content"] == "问7"
        assert messages[-1]["content"] == "答9"

    async def test_unknown_session_returns_empty(self, db):
        assert await db.get_recent_turns("no-such", limit=10) == []

    async def test_user_id_filtered(self, db):
        """不属于 user_id 的会话返回空"""
        await _save_turn(db, "s3", 0, "问0", "答0", user_id="alice")
        assert await db.get_recent_turns("s3", user_id="bob", limit=10) == []
        assert len(await db.get_recent_turns("s3", user_id="alice", limit=10)) == 2


async def test_restore_uses_complete_mysql_watermark(db, stm):
    for index in range(12):
        await _save_turn(db, "s", index, f"问{index}", f"答{index}")
    messages = await db.get_recent_turns("s", limit=None)
    assert await stm.restore_session("s", messages)
    assert len(await stm.get_all_messages("s")) == 24
    assert not await stm.restore_session("s", messages)
    await _save_turn(db, "s", 12, "新问题", "新回答")
    assert await stm.restore_session("s", await db.get_recent_turns("s", limit=None))
    assert len(await stm.get_all_messages("s")) == 26


async def test_empty_cache_initializes_once(stm):
    assert await stm.restore_session("s", [])
    assert not await stm.restore_session("s", [])
