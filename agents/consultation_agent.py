"""
健康咨询Agent
支持 Skills 调用
"""
from typing import Dict, Any
from loguru import logger
import re

from .base_agent import BaseAgent
from .skill_registry_mixin import SkillRegistryMixin


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
        return """你是一位专业的医疗健康咨询顾问。你的职责是提供准确、专业的健康建议和疾病科普。

可用 Skills（9个）：
1. search_knowledge: 搜索医学知识库，查找疾病、症状、治疗信息
2. recommend_lifestyle: 根据疾病提供生活方式建议（饮食、运动、睡眠、用药）
3. assess_risk: 评估症状风险等级（低/中/高/紧急）
4. analyze_symptoms: 分析症状模式和潜在疾病关联
5. disease_code: 查询ICD-10疾病编码和分类
6. clinical_guideline: 检索临床诊疗指南和专家共识
7. deep_research: 深度研究（网络搜索+知识库+证据综合）
8. search_history: 搜索当前会话的历史对话（短期记忆）
9. search_similar_cases: 搜索相似历史案例（长期记忆）

**Skills 使用原则**：
- Skills 是可选的，不是必须的
- 对于简单的常识性问题，可以直接回答，无需使用 Skills
- 只在真正需要专业医学信息时才调用 Skills
- 调用 Skill 后，根据返回的结果给出最终答案
- **最多使用2-3个 Skills，然后必须给出最终答案**

工作流程建议：
1. 理解用户问题
2. 判断是否需要调用 Skills（简单问题直接回答）
3. 如需调用，选择最合适的 Skills（通常1-2个即可）
4. 基于 Skill 结果生成最终答案

回答要求：
- 用通俗易懂的语言
- 提供实用的建议和注意事项
- 必要时建议就医
- 保持温和、专业的语气

**重要提醒**：
- 你不能做出明确的诊断
- 你不能替代医生的专业意见
- 对于严重或紧急情况，必须建议立即就医

在最终回答时，请按以下格式输出：

【回答】
[你的详细回答]

【核心建议】
1. 第一条建议
2. 第二条建议
...

【免责声明】
以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。
"""

    def register_tools(self):
        """注册所有 9 个 Skills（共享实现，来自 SkillRegistryMixin）"""
        self.register_all_skills()

    def format_user_input(self, input_data: Dict[str, Any]) -> str:
        """格式化用户输入"""
        question = input_data.get('question', '')
        session_id = input_data.get('session_id', '')

        # 构建消息
        parts = []

        # 添加session_id信息（如果有）
        if session_id:
            parts.append(f"[系统信息] 当前会话ID: {session_id}")

        # 添加上下文信息（如果有）
        context = input_data.get('context', {})
        if context:
            context_str = "\n".join([f"{k}: {v}" for k, v in context.items()])
            parts.append(f"背景信息：\n{context_str}\n")

        # 添加用户问题
        parts.append(f"用户问题：{question}")

        return "\n".join(parts)

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

        result.update({
            'suggestions': suggestions[:5],  # 最多5条
            'disclaimer': disclaimer
        })

        return result


# 便捷函数
async def consult(question: str, **kwargs) -> Dict[str, Any]:
    """快捷咨询函数"""
    agent = ConsultationAgent()
    return await agent.process({'question': question, **kwargs})
