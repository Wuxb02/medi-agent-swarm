"""test_agents/test_agent_capabilities.py — Agent 能力标签与工具注册数单元测试"""

import pytest
from unittest.mock import MagicMock, patch

from agents.consultation_agent import ConsultationAgent
from agents.diagnostic_agent import DiagnosticAgent
from agents.research_agent import ResearchAgent


def _make_agent(agent_cls):
    """构造 Agent 实例（最小初始化，避免真实 LLM 调用）。"""
    agent = agent_cls.__new__(agent_cls)
    # 模拟 SkillRegistry
    mock_registry = MagicMock()
    mock_registry.get_skills_catalog.return_value = "- **search-knowledge**: 搜索知识库（1个工具）"
    agent.skill_registry = mock_registry
    agent.active_skill = None
    agent.agent_id = agent_cls.__name__
    agent.capabilities = []
    return agent


def _make_full_agent(agent_cls):
    """通过真实构造器创建 Agent（mock LLMClient）。"""
    with patch("core.llm_client.AsyncOpenAI", autospec=True):
        agent = agent_cls()
    return agent


class TestConsultationAgent:
    def test_agent_id(self):
        agent = _make_full_agent(ConsultationAgent)
        assert isinstance(agent.agent_id, str)
        assert len(agent.agent_id) > 0
        assert "consult" in agent.agent_id.lower()

    def test_set_and_get_capabilities(self):
        agent = _make_agent(ConsultationAgent)
        agent.set_capabilities(["健康咨询", "风险评估"])
        caps = agent.get_capabilities()
        assert len(caps) == 2
        assert "健康咨询" in caps

    def test_get_system_prompt(self):
        agent = _make_full_agent(ConsultationAgent)
        prompt = agent.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestDiagnosticAgent:
    def test_agent_id(self):
        agent = _make_full_agent(DiagnosticAgent)
        assert isinstance(agent.agent_id, str)
        assert "diagnos" in agent.agent_id.lower()

    def test_set_and_get_capabilities(self):
        agent = _make_agent(DiagnosticAgent)
        agent.set_capabilities(["症状分析", "鉴别诊断"])
        caps = agent.get_capabilities()
        assert "症状分析" in caps

    def test_get_system_prompt(self):
        agent = _make_full_agent(DiagnosticAgent)
        prompt = agent.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestResearchAgent:
    def test_agent_id(self):
        agent = _make_full_agent(ResearchAgent)
        assert isinstance(agent.agent_id, str)
        assert "research" in agent.agent_id.lower()

    def test_set_and_get_capabilities(self):
        agent = _make_agent(ResearchAgent)
        agent.set_capabilities(["文献检索", "证据综合"])
        caps = agent.get_capabilities()
        assert "文献检索" in caps

    def test_get_system_prompt(self):
        agent = _make_full_agent(ResearchAgent)
        prompt = agent.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestAgentCrossCheck:
    def test_agents_have_unique_ids(self):
        agents = [_make_full_agent(c) for c in [ConsultationAgent, DiagnosticAgent, ResearchAgent]]
        ids = [a.agent_id for a in agents]
        assert len(ids) == len(set(ids))

    def test_all_agents_have_capabilities_after_init(self):
        for cls in [ConsultationAgent, DiagnosticAgent, ResearchAgent]:
            agent = _make_full_agent(cls)
            caps = agent.get_capabilities()
            assert isinstance(caps, list), f"{cls.__name__} capabilities not a list"
