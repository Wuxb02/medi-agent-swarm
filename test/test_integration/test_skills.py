"""test_integration/test_skills.py — 技能集成测试（需要真实 LLM + Milvus）"""

import pytest
from agents import ConsultationAgent, DiagnosticAgent, ResearchAgent


@pytest.mark.integration
@pytest.mark.slow
class TestSkillsIntegration:
    @pytest.mark.asyncio
    async def test_recommend_lifestyle(self):
        """生活方式建议。"""
        agent = ConsultationAgent()
        result = await agent.process({
            "question": "我有高血压，应该如何调整生活方式和用药？",
            "context": {"age": 55, "diagnosis": "高血压"},
        })
        assert "answer" in result or "response" in result
        answer = result.get("answer", result.get("response", ""))
        assert any(kw in answer for kw in ["饮食", "运动", "生活", "用药", "药物"])

    @pytest.mark.asyncio
    async def test_disease_classification(self):
        """疾病分类（ICD-10）。"""
        agent = DiagnosticAgent()
        result = await agent.process({
            "question": "高血压的ICD-10编码是什么？",
        })
        answer = result.get("answer", result.get("response", ""))
        assert len(answer) > 10

    @pytest.mark.asyncio
    async def test_clinical_guidelines(self):
        """临床指南检索。"""
        agent = ResearchAgent()
        result = await agent.process({
            "question": "2024年高血压治疗的最新临床指南是什么？",
        })
        answer = result.get("answer", result.get("response", ""))
        assert len(answer) > 10
