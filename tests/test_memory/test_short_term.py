"""真实 Redis 的短期记忆基础操作与用户隔离。"""

import pytest
from mediZJ.memory.short_term import ShortTermMemory, ConversationHistory

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


@pytest.fixture
def stm(mysql_infrastructure):
    return ShortTermMemory("alice")


async def test_create_and_get_session(stm):
    history = await stm.create_session("s1", {"purpose": "test"})
    assert history.session_id == "s1"
    assert history.messages == []
    assert (await stm.get_session("s1")).metadata == {"purpose": "test"}
    assert await stm.get_session("missing") is None
    await stm.add_message("s1", "user", "问题")
    assert (await stm.create_session("s1")).messages[0]["content"] == "问题"


async def test_messages_and_recent_limits(stm):
    for index in range(10):
        await stm.add_message("s1", "user", f"msg-{index}")
    assert len(await stm.get_all_messages("s1")) == 10
    assert len(await stm.get_recent_messages("s1", 3)) == 3
    assert (await stm.get_history("s1", 1))[-1]["content"] == "msg-9"
    assert await stm.get_recent_messages("missing") == []


async def test_user_and_session_isolation_and_delete(stm):
    await stm.add_message("s1", "user", "alice")
    await stm.add_message("s2", "user", "second")
    other = ShortTermMemory("bob")
    assert await other.get_session("s1") is None
    await other.add_message("s1", "user", "bob")
    await stm.clear_session("s1")
    assert await stm.get_session("s1") is None
    assert (await other.get_session("s1")).messages[0]["content"] == "bob"
    assert (await stm.get_session("s2")).messages[0]["content"] == "second"


async def test_merge_sub_session(stm):
    await stm.add_message("sub", "user", "详情")
    await stm.merge_sub_session("main", "sub", "摘要")
    assert await stm.get_session("sub") is None
    assert (await stm.get_history("main"))[0]["content"] == "摘要"


def test_snapshot_round_trip():
    history = ConversationHistory("s")
    history.add_message("user", "内容")
    restored = ConversationHistory.from_dict(history.to_dict())
    assert restored.to_dict() == history.to_dict()
    assert restored.get_recent_messages(None) == history.messages
