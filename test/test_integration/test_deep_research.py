"""test_integration/test_deep_research.py — 深度研究集成测试（需要真实 LLM + 网络）"""

import pytest
from agents import ResearchAgent


@pytest.mark.integration
@pytest.mark.slow
class TestDeepResearchIntegration:
    @pytest.mark.asyncio
    async def test_end_to_end(self):
        """深度研究端到端测试。"""
        agent = ResearchAgent()
        result = await agent.process({
            "question": "最新的糖尿病治疗方法有哪些？请基于临床证据。",
        })
        assert "answer" in result or "response" in result
        answer = result.get("answer", result.get("response", ""))
        assert len(answer) > 20
