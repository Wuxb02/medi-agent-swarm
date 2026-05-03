"""
健康咨询Agent
支持 Skills 调用
"""
from typing import Dict, Any
from loguru import logger
import re

from .base_agent import BaseAgent
from .skill_registry_mixin import SkillRegistryMixin
from core.prompt_loader import PromptLoader


class ConsultationAgent(BaseAgent, SkillRegistryMixin):
    """
    健康咨询Agent
    通过 Skills 调用底层工具
    """

    def __init__(self, config: Dict[str, Any] = None):
        default_config = {
            "model": "openai_compatible",
            "max_iterations": 5,
            "temperature": 0.8,
            "description": "健康咨询Agent，提供通用医疗咨询和健康建议"
        }

        config = config or default_config
        super().__init__(
            agent_id="consultation_agent",
            config=config
        )

        # 设置能力标签（Swarm 协作用）
        self.set_capabilities([
            "general_health_advice",
            "risk_assessment",
            "symptom_triage"
        ])

    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return PromptLoader.load("agents/consultation_system.j2")

    def register_tools(self):
        """注册所有 9 个 Skills（共享实现，来自 SkillRegistryMixin）"""
        self.register_all_skills()

    def format_user_input(self, input_data: Dict[str, Any]) -> str:
        """格式化用户输入"""
        return PromptLoader.render(
            "agents/consultation_user_input.j2",
            question=input_data.get('question', ''),
            session_id=input_data.get('session_id', ''),
            context=input_data.get('context', {}),
        )

    async def post_process_result(
        self,
        result: Dict[str, Any],
        final_response: str
    ) -> Dict[str, Any]:
        """
        后处理：从最终响应中提取结构化信息
        """
        # 提取核心建议
        suggestions = []
        suggestion_pattern = r'【核心建议】\s*\n((?:\d+\.\s*.+\n?)+)'
        match = re.search(suggestion_pattern, final_response)

        if match:
            suggestion_text = match.group(1)
            suggestion_lines = re.findall(r'\d+\.\s*(.+)', suggestion_text)
            suggestions = [s.strip() for s in suggestion_lines if s.strip()]

        # 提取免责声明
        disclaimer_pattern = r'【免责声明】\s*\n(.+)'
        disclaimer_match = re.search(disclaimer_pattern, final_response)
        disclaimer = disclaimer_match.group(1) if disclaimer_match else \
            "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"

        # 清理答案文本：去掉【回答】前缀和结构化标签段
        clean_answer = final_response
        # 去掉【回答】前缀（兼容旧格式）
        clean_answer = re.sub(r'^【回答】\s*\n?', '', clean_answer)
        # 去掉【核心建议】及其内容
        clean_answer = re.sub(r'\n?【核心建议】[\s\S]*$', '', clean_answer)
        # 去掉【免责声明】及其内容
        clean_answer = re.sub(r'\n?【免责声明】[\s\S]*$', '', clean_answer)
        clean_answer = clean_answer.strip()

        result.update({
            'answer': clean_answer,
            'suggestions': suggestions[:5],  # 最多5条
            'disclaimer': disclaimer
        })

        return result


# 便捷函数
async def consult(question: str, **kwargs) -> Dict[str, Any]:
    """快捷咨询函数"""
    agent = ConsultationAgent()
    return await agent.process({'question': question, **kwargs})
