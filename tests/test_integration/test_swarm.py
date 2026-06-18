"""test_integration/test_swarm.py — Swarm 集成测试（需要真实 LLM）"""

import pytest
import asyncio
from mediZJ.swarm import SwarmCoordinator, process_with_swarm


@pytest.mark.integration
@pytest.mark.slow
class TestSwarmIntegration:
    @pytest.mark.asyncio
    async def test_simple_routing_single_agent(self):
        """简单问题路由到单个 Agent。"""
        coordinator = SwarmCoordinator()
        result = await coordinator.process(
            question="我今天有点头疼，应该注意什么？",
            session_id="test-routing-simple",
        )
        assert isinstance(result, dict)
        assert "response" in result or "answer" in result

    @pytest.mark.asyncio
    async def test_complex_case_swarm(self):
        """复杂医疗案例触发 Swarm 协作。"""
        coordinator = SwarmCoordinator()
        result = await coordinator.process(
            question="我同时有胸痛、呼吸困难和心悸，还有高血压和糖尿病史，应该怎么办？",
            session_id="test-swarm-complex",
        )
        assert isinstance(result, dict)
        assert "response" in result or "answer" in result

    @pytest.mark.asyncio
    async def test_session_summary(self):
        """会话摘要生成。"""
        coordinator = SwarmCoordinator()
        session_id = "test-session-summary"

        # 产生一些对话
        await coordinator.process(
            question="高血压患者饮食需要注意什么？",
            session_id=session_id,
        )
        await coordinator.process(
            question="运动方面有什么建议？",
            session_id=session_id,
        )

        # 获取历史记录
        history = await coordinator.short_term_memory.get_history(session_id, limit=10)
        assert len(history) >= 2

    @pytest.mark.asyncio
    async def test_backward_compatibility(self):
        """向后兼容性：process_with_swarm 函数仍可用。"""
        result = await process_with_swarm(
            question="普通感冒有什么症状？",
            session_id="test-backward-compat",
        )
        assert isinstance(result, dict)
        assert "response" in result or "answer" in result
