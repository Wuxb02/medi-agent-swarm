"""
ResearchAgent：医学文献检索和证据支持 Agent

职责：
- 搜索医学文献和临床指南
- 提供循证医学证据
- 验证其他 Agent 的结论
- 提供文献来源和证据等级
"""
from typing import Dict, Any, Optional
from loguru import logger

from .base_agent import BaseAgent
from .skill_registry_mixin import SkillRegistryMixin
from mediZJ.core import LLMClient
from mediZJ.core.prompt_loader import PromptLoader


class ResearchAgent(BaseAgent, SkillRegistryMixin):
    """
    研究 Agent

    职责：
    - 检索医学文献和临床指南
    - 提取关键证据支持诊疗决策
    - 验证医学结论
    - 提供证据等级（A/B/C 级）

    能力标签：
    - literature_search
    - evidence_synthesis
    - fact_checking
    - guideline_lookup
    """

    def __init__(
        self,
        agent_id: str = "research_agent",
        config: Optional[Dict[str, Any]] = None,
        llm_client: Optional[LLMClient] = None
    ):
        config = config or {}
        config.setdefault('max_iterations', 5)

        super().__init__(agent_id, config, llm_client)

        # 设置能力标签
        self.set_capabilities([
            "literature_search",
            "evidence_synthesis",
            "fact_checking",
            "guideline_lookup",
            "deep_research",  
            "latest_information" 
        ])

    def register_tools(self):
        """注册所有 9 个 Skills（共享实现，来自 SkillRegistryMixin）"""
        self.register_all_skills()


    def _get_base_system_prompt(self) -> str:
        """获取基础系统提示词（不含 Skill 信息）"""
        return PromptLoader.load("agents/research_system.j2")

    async def post_process_result(
        self,
        result: Dict[str, Any],
        final_response: str
    ) -> Dict[str, Any]:
        """
        结果后处理：提取文献引用和证据等级

        这里可以添加更复杂的解析逻辑
        """
        # 尝试识别证据等级
        evidence_level = "unknown"
        if "A级" in final_response or "A 级" in final_response:
            evidence_level = "A"
        elif "B级" in final_response or "B 级" in final_response:
            evidence_level = "B"
        elif "C级" in final_response or "C 级" in final_response:
            evidence_level = "C"

        # 统计文献数量
        literature_count = final_response.count("文献")

        result.update({
            "evidence_level": evidence_level,
            "literature_count": literature_count,
            "evidence_provided": True
        })

        return result



# 便捷函数
async def research(question: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    便捷函数：快速使用 ResearchAgent

    Args:
        question: 研究问题或查询
        context: 额外上下文（其他 Agent 的结果等）

    Returns:
        研究结果和文献证据
    """
    agent = ResearchAgent()
    input_data = {'question': question}
    if context:
        input_data['context'] = context

    return await agent.process(input_data)
