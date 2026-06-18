"""test_memory/test_short_term.py — ShortTermMemory 单元测试"""

import pytest
from mediZJ.memory.short_term import ShortTermMemory


@pytest.fixture(autouse=True)
def _reset_singleton():
    ShortTermMemory._instance = None
    yield
    ShortTermMemory._instance = None


class TestSingleton:
    def test_singleton_instance(self):
        ShortTermMemory._instance = None
        stm1 = ShortTermMemory(storage_type="memory")
        stm2 = ShortTermMemory()
        assert stm1 is stm2

    def test_reset_singleton(self):
        ShortTermMemory._instance = None
        stm1 = ShortTermMemory(storage_type="memory")
        ShortTermMemory._instance = None
        stm2 = ShortTermMemory(storage_type="memory")
        assert stm1 is not stm2


class TestSessionManagement:
    @pytest.fixture
    def stm(self):
        ShortTermMemory._instance = None
        return ShortTermMemory(storage_type="memory")

    def test_create_session(self, stm):
        history = stm.create_session("sess-1")
        assert history.session_id == "sess-1"
        assert history.messages == []

    def test_get_session(self, stm):
        stm.create_session("sess-1")
        sess = stm.get_session("sess-1")
        assert sess is not None
        assert sess.session_id == "sess-1"

    def test_get_session_nonexistent(self, stm):
        assert stm.get_session("no-exist") is None

    @pytest.mark.asyncio
    async def test_add_message_to_existing_session(self, stm):
        stm.create_session("sess-1")
        await stm.add_message("sess-1", "user", "测试消息")
        messages = await stm.get_history("sess-1")
        assert len(messages) == 1
        assert messages[0]["content"] == "测试消息"

    @pytest.mark.asyncio
    async def test_add_message_auto_creates_session(self, stm):
        await stm.add_message("new-session", "user", "hello")
        history = stm.get_session("new-session")
        assert history is not None
        assert history.session_id == "new-session"

    @pytest.mark.asyncio
    async def test_get_recent_messages(self, stm):
        stm.create_session("sess-1")
        history = stm.get_session("sess-1")
        for i in range(10):
            history.add_message("user", f"msg-{i}")
        messages = await stm.get_recent_messages("sess-1", limit=3)
        assert len(messages) == 3
        assert messages[-1]["content"] == "msg-9"

    @pytest.mark.asyncio
    async def test_get_recent_nonexistent_session(self, stm):
        messages = await stm.get_recent_messages("no-such-session")
        assert messages == []

    @pytest.mark.asyncio
    async def test_multiple_sessions_isolated(self, stm):
        await stm.add_message("s1", "user", "msg-1")
        await stm.add_message("s2", "user", "msg-2")
        msgs1 = await stm.get_history("s1")
        msgs2 = await stm.get_history("s2")
        assert len(msgs1) == 1
        assert len(msgs2) == 1
        assert msgs1[0]["content"] == "msg-1"
        assert msgs2[0]["content"] == "msg-2"

    def test_clear_session(self, stm):
        stm.create_session("sess-1")
        history = stm.get_session("sess-1")
        history.add_message("user", "test")
        stm.clear_session("sess-1")
        assert stm.get_session("sess-1") is None
