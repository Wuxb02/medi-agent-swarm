"""
DiagnosticAgent：症状诊断推理 Agent

这是第一个 WorkerAgent 实现，展示如何：
1. 参与 Swarm 协作
2. 自主认领任务
3. 调用医疗工具
4. 将结果写入 SharedContext
"""
from typing import Dict, Any, Optional
from loguru import logger

from .base_agent import BaseAgent
from .skill_registry_mixin import SkillRegistryMixin
from core import LLMClient
from core.prompt_loader import PromptLoader


class DiagnosticAgent(BaseAgent, SkillRegistryMixin):
    """
    诊断 Agent

    职责：
    - 复杂症状的鉴别诊断
    - 多系统关联分析
    - 诊断思路推理（类似医生的临床思维）

    能力标签：
    - symptom_analysis
    - differential_diagnosis
    - clinical_reasoning
    """

    def __init__(
        self,
        agent_id: str = "diagnostic_agent",
        config: Optional[Dict[str, Any]] = None,
        llm_client: Optional[LLMClient] = None
    ):
        config = config or {}
        config.setdefault('max_iterations', 5)

        super().__init__(agent_id, config, llm_client)

        # 设置能力标签（Swarm 协作用）
        self.set_capabilities([
            "symptom_analysis",
            "differential_diagnosis",
            "clinical_reasoning",
            "multi_system_analysis"
        ])

    def register_tools(self):
        """注册所有 9 个 Skills（共享实现，来自 SkillRegistryMixin）"""
        self.register_all_skills()


    def _get_base_system_prompt(self) -> str:
        """获取基础系统提示词（不含 Skill 信息）"""
        return PromptLoader.load("agents/diagnostic_system.j2")

    async def post_process_result(
        self,
        result: Dict[str, Any],
        final_response: str
    ) -> Dict[str, Any]:
        """
        结果后处理：提取结构化诊断信息

        这里可以添加更复杂的解析逻辑
        """
        # 尝试提取风险等级
        risk_level = "unknown"
        if "风险等级" in final_response:
            if "高" in final_response or "HIGH" in final_response:
                risk_level = "high"
            elif "中" in final_response or "MEDIUM" in final_response:
                risk_level = "medium"
            elif "低" in final_response or "LOW" in final_response:
                risk_level = "low"

        result.update({
            "risk_level": risk_level,
            "diagnosis_provided": True
        })

        return result


# 便捷函数
async def diagnose(question: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    便捷函数：快速使用 DiagnosticAgent

    Args:
        question: 症状描述
        context: 额外上下文（年龄、既往史等）

    Returns:
        诊断结果
    """
    agent = DiagnosticAgent()
    input_data = {'question': question}
    if context:
        input_data['context'] = context

    return await agent.process(input_data)
