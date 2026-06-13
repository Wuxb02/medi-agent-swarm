"""test_integration/test_agent_loop.py — Agent Loop 集成测试（需要真实 LLM）"""

import pytest
from agents import ConsultationAgent


@pytest.mark.integration
@pytest.mark.slow
class TestAgentLoopIntegration:
    @pytest.mark.asyncio
    async def test_simple_question(self):
        """简单问题应答（无工具调用）。"""
        agent = ConsultationAgent()
        result = await agent.process({
            "question": "什么是高血压？请用一两句话简单介绍。",
        })
        assert isinstance(result, dict)
        assert "answer" in result or "response" in result
        answer = result.get("answer", result.get("response", ""))
        assert len(answer) > 10

    @pytest.mark.asyncio
    async def test_symptom_consultation_with_tools(self):
        """症状咨询（有工具调用）。"""
        agent = ConsultationAgent()
        result = await agent.process({
            "question": "我最近总是头痛，应该怎么办？",
            "context": {"age": 30, "gender": "男"},
        })
        assert isinstance(result, dict)
        assert "answer" in result or "response" in result
        answer = result.get("answer", result.get("response", ""))
        assert len(answer) > 20
