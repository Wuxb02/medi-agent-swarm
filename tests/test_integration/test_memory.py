"""test_integration/test_memory.py — 记忆系统集成测试（需要真实 LLM + Mem0）"""

import pytest
import os
from mediZJ.swarm import SwarmCoordinator


@pytest.mark.integration
@pytest.mark.slow
class TestMemoryIntegration:
    @pytest.mark.asyncio
    async def test_multiturn_context(self):
        """多轮对话上下文感知。"""
        coordinator = SwarmCoordinator()
        session_id = "test-multi-turn"

        # 第1轮：建立上下文
        r1 = await coordinator.process(
            question="我感冒了，有咳嗽和发烧症状。",
            session_id=session_id,
        )
        assert isinstance(r1, dict)

        # 第2轮：引用之前的上下文
        r2 = await coordinator.process(
            question="我之前提到的发热，需要用退烧药吗？",
            session_id=session_id,
        )
        answer2 = r2.get("response", r2.get("answer", ""))
        assert len(answer2) > 10

        # 验证历史消息数量
        history = await coordinator.short_term_memory.get_history(session_id, limit=10)
        assert len(history) >= 4  # 2轮对话 = 4条消息

    @pytest.mark.asyncio
    async def test_unified_memory_single_agent(self):
        """单 Agent 的短期记忆记录。"""
        coordinator = SwarmCoordinator()
        session_id = "test-unified-single"

        await coordinator.process(
            question="高血压是什么？",
            session_id=session_id,
        )
        history = await coordinator.short_term_memory.get_history(session_id)
        assert len(history) >= 1

    @pytest.mark.asyncio
    async def test_unified_memory_swarm(self):
        """Swarm 模式的短期记忆记录。"""
        coordinator = SwarmCoordinator()
        session_id = "test-unified-swarm"

        await coordinator.process(
            question="胸痛、呼吸困难、心悸，应该怎么办？有高血压史。",
            session_id=session_id,
        )
        history = await coordinator.short_term_memory.get_history(session_id)
        assert len(history) >= 1
