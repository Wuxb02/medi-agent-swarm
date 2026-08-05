"""test_core/test_questionnaire_manager.py — QuestionnaireManager 单元测试"""

import pytest
import asyncio
from mediZJ.core.questionnaire_manager import QuestionnaireManager


class TestQuestionnaireManager:
    @pytest.fixture
    def qm(self):
        return QuestionnaireManager()

    def test_init_no_pending(self, qm):
        assert qm.has_pending is False
        assert qm.pending_ids == []

    @pytest.mark.asyncio
    async def test_create_and_resolve(self, qm):
        # 模拟创建 + 异步解析
        async def resolver():
            await asyncio.sleep(0.01)
            qm.resolve("q1", {"q0": "yes"})

        task = asyncio.ensure_future(resolver())
        result = await qm.create_pending("q1", timeout=1.0)
        await task
        assert result == {"q0": "yes"}
        assert qm.has_pending is False

    @pytest.mark.asyncio
    async def test_timeout(self, qm):
        with pytest.raises(TimeoutError):
            await qm.create_pending("q2", timeout=0.01)

    @pytest.mark.asyncio
    async def test_no_timeout_waits_forever_until_resolved(self, qm):
        """timeout=None 时无限等待，直到用户回答才返回。"""
        task = asyncio.ensure_future(qm.create_pending("q-no-timeout", timeout=None))
        # 给一点时间确认 future 已创建且未超时返回
        await asyncio.sleep(0.05)
        assert task.done() is False
        # 用户回答后恢复
        qm.resolve("q-no-timeout", {"q0": "answer"})
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == {"q0": "answer"}
        assert qm.has_pending is False

    @pytest.mark.asyncio
    async def test_resolve_after_timeout_returns_false(self, qm):
        try:
            await qm.create_pending("q3", timeout=0.01)
        except TimeoutError:
            pass
        # Future 应该已被清理
        assert qm.resolve("q3", {"a": "b"}) is False

    def test_resolve_nonexistent(self, qm):
        assert qm.resolve("no-such", {"a": "b"}) is False

    def test_cancel(self, qm):
        assert qm.cancel("no-such") is False

    @pytest.mark.asyncio
    async def test_cancel_active(self, qm):
        async def run_and_cancel():
            await asyncio.sleep(0.02)
            qm.cancel("q4")

        task = asyncio.ensure_future(run_and_cancel())
        with pytest.raises(asyncio.CancelledError):
            await qm.create_pending("q4", timeout=1.0)
        await task

    @pytest.mark.asyncio
    async def test_cancel_all(self, qm):
        # 在 async 上下文中创建 futures
        loop = asyncio.get_event_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        qm._pending["q5"] = f1
        qm._pending["q6"] = f2
        qm.cancel_all()
        assert qm.has_pending is False
        assert f1.cancelled() or f1.done()
        assert f2.cancelled() or f2.done()

    @pytest.mark.asyncio
    async def test_pending_ids_property(self, qm):
        loop = asyncio.get_event_loop()
        qm._pending["a"] = loop.create_future()
        qm._pending["b"] = loop.create_future()
        assert set(qm.pending_ids) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_double_resolve_returns_false(self, qm):
        async def resolver():
            await asyncio.sleep(0.01)
            ok = qm.resolve("q7", {"q": "1"})
            assert ok is True
            # 第二次解析应该失败
            ok2 = qm.resolve("q7", {"q": "2"})
            assert ok2 is False

        task = asyncio.ensure_future(resolver())
        result = await qm.create_pending("q7", timeout=1.0)
        await task
        assert result["q"] == "1"
