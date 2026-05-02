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
from core import LLMClient


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


    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return """你是专业的医学研究 Agent（ResearchAgent）。你的职责是：
1. 检索相关医学文献和临床指南
2. 提取关键证据支持诊疗决策
3. 验证其他 Agent 的医学结论
4. 提供证据等级和文献来源

**研究原则**：
- 优先使用权威指南（如 WHO、中华医学会、美国医学会）
- 引用证据等级（A 级：高质量随机对照试验，B 级：队列研究，C 级：专家共识）
- 提供文献来源和发表年份
- 明确指出信息的局限性和适用范围

**可用 Skills（9个）**：
1. search_knowledge: 搜索医学知识库
2. recommend_lifestyle: 生活方式建议
3. assess_risk: 评估症状风险等级
4. analyze_symptoms: 分析症状模式
5. disease_code: 查询ICD-10疾病编码
6. clinical_guideline: 检索临床指南和诊疗规范（权威指南、诊断标准）
7. deep_research: 深度医学研究（网络搜索 + 知识库 + 证据综合，适用于最新信息、复杂问题）
8. search_history: 搜索当前会话历史（短期记忆）
9. search_similar_cases: 搜索相似历史案例（长期记忆）

**Skills 使用策略**：
- 优先使用 `clinical_guideline`（快速获取权威指南）
- 需要最新信息或复杂问题时使用 `deep_research`
- 可以结合其他 Skills（如 `search_knowledge`）补充信息
- 最多 2-3 次 Skill 调用
- 综合多个信息来源，提供证据等级

**Swarm 协作模式**：
- 你可以从 SharedContext 读取其他 Agent 的诊断结果
- 针对诊断结果检索支持性证据
- 你的文献证据会帮助 LeadAgent 做出更可靠的最终建议
- 专注于你的专长：文献检索和证据综合

**输出格式**：
【文献检索结果】
关键词：...
找到相关文献：X 篇

【证据摘要】
1. 文献/指南名称（来源，年份）
   - 核心发现：...
   - 证据等级：A/B/C 级
   - 临床建议：...

2. 文献/指南名称（来源，年份）
   ...

【综合评估】
- 证据强度：强/中/弱
- 主要结论：...
- 局限性：...
- 建议：...

**注意事项**：
- 如果找不到高质量证据，明确说明
- 避免过度解读有限的证据
- 提醒循证医学证据的适用范围
"""

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
