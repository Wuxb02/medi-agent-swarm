"""
Agent基类
支持 LLM 驱动的 Skill 调用 + Swarm 协作
双层架构：Skill（能力包）= 指令正文 + 工具函数
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from loguru import logger

from core import LLMClient, AgentLoop
from core.skill_registry import SkillRegistry, SkillParameter


class BaseAgent(ABC):
    """
    Agent基类
    子类需要实现：
    - _get_base_system_prompt(): 返回基础系统提示词（不含 Skill 信息）
    - register_tools(): 注册 Agent 的 Skills
    - process(): 主入口（可选，默认使用 run_loop）
    """

    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        llm_client: Optional[LLMClient] = None
    ):
        self.agent_id = agent_id
        self.config = config
        self.llm_client = llm_client or LLMClient(model_type=config.get('model', 'openai_compatible'))
        self.loop = AgentLoop(max_iterations=config.get('max_iterations', 10))

        # Skill 注册表
        self.skill_registry = SkillRegistry()

        # 检测兼容模式
        skill_mode = config.get('skill_mode', 'auto')
        if skill_mode == 'flat':
            self.skill_registry.set_compat_mode(True)
        elif skill_mode == 'layered':
            self.skill_registry.set_compat_mode(False)
        # 'auto' 模式在 register_tools 后由 mixin 自动检测

        # 注册基础工具
        self._register_base_tools()

        # 注册 Skills（由子类实现）
        self.register_tools()

        # 自动检测兼容模式（auto 模式下）
        if skill_mode == 'auto':
            if self.skill_registry.has_migrated_skills():
                self.skill_registry.set_compat_mode(False)
                logger.info(f"All skills migrated, using layered mode")
            else:
                self.skill_registry.set_compat_mode(True)
                logger.info(f"Some skills not migrated, using compat mode")

        # Swarm 协作相关
        self.capabilities: List[str] = []  # 能力标签
        self.shared_context: Optional[Any] = None  # SharedContext 引用
        self.identity_manager: Optional[Any] = None  # AgentIdentityManager 引用

        logger.info(
            f"Initialized {self.__class__.__name__} (id={agent_id}) "
            f"with {len(self.skill_registry.get_all())} skills "
            f"(compat_mode={self.skill_registry.compat_mode})"
        )

    def _register_base_tools(self):
        """注册始终可用的基础工具"""
        from core.tools import create_activate_skill_tool, create_question_for_user_tool

        # 注册 activate_skill
        activate_skill_func = create_activate_skill_tool(self.skill_registry)

        self.skill_registry.register_base_tool(
            name="activate_skill",
            function=activate_skill_func,
            description="激活指定 Skill。激活后可以使用该 Skill 的工具。同一时间只能有一个 Skill 处于激活状态。",
            parameters=[SkillParameter(
                name="name",
                type="string",
                description="Skill 名称（如 search-knowledge, deep-research）",
                required=True
            )]
        )

        # 注册 question_for_user（交互式问卷工具）
        def _get_manager():
            return getattr(self.loop, 'questionnaire_manager', None)

        q_func = create_question_for_user_tool(_get_manager)

        self.skill_registry.register_base_tool(
            name="question_for_user",
            function=q_func,
            description=(
                "向用户发送结构化问卷，收集诊断所需信息。"
                "支持单选(enum)、多选(multi)、文本输入(input)三种题型。"
                "在诊断前收集患者背景信息时使用此工具。"
            ),
            parameters=[SkillParameter(
                name="questionnaire",
                type="string",
                description=(
                    "XML 格式的问卷，包含 <questions> 标签包裹的 <question> 元素。"
                    "每个问题有 header（标题）、type（enum/multi/input）、"
                    "text（问题文本）和 suggest（选项）。"
                    "例：<questions><question header='年龄' type='input'>"
                    "<text>您的年龄是？</text></question></questions>"
                ),
                required=True
            )]
        )

    @abstractmethod
    def _get_base_system_prompt(self) -> str:
        """
        获取基础系统提示词（不含 Skill 信息）
        子类必须实现
        """
        pass

    def get_system_prompt(self) -> str:
        """
        获取完整的系统提示词
        包含：基础 prompt + Skills 目录 + 当前激活 Skill 的指令正文
        """
        base = self._get_base_system_prompt()

        # 双层模式下拼接 Skills 目录
        if not self.skill_registry.compat_mode:
            catalog = self.skill_registry.get_skills_catalog()
            base += f"\n\n---\n## 可用 Skills\n{catalog}\n\n使用 activate_skill(name=\"xxx\") 激活技能后方可使用其工具。"

            # 如果有激活的 Skill，追加其指令正文
            instructions = self.skill_registry.get_active_instructions()
            if instructions:
                base += f"\n\n---\n## 当前 Skill 指令\n{instructions}"

        return base

    @abstractmethod
    def register_tools(self):
        """注册 Agent 的 Skills（子类必须实现）"""
        pass

    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        """获取 OpenAI function calling 格式的工具列表"""
        return self.skill_registry.to_openai_format()

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果
        """
        return await self.skill_registry.execute(tool_name, **arguments)

    def format_user_input(self, input_data: Dict[str, Any]) -> str:
        """
        格式化用户输入
        子类可以重写

        Args:
            input_data: 输入数据

        Returns:
            格式化后的用户消息
        """
        # 默认实现
        if 'question' in input_data:
            return input_data['question']
        elif 'query' in input_data:
            return input_data['query']
        else:
            return str(input_data)

    async def post_process_result(
        self,
        result: Dict[str, Any],
        final_response: str
    ) -> Dict[str, Any]:
        """
        结果后处理
        子类可以重写来提取结构化信息

        Args:
            result: 初始结果
            final_response: LLM 的最终响应

        Returns:
            处理后的结果
        """
        # 默认不做额外处理
        return result

    async def process(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理输入数据
        默认实现：运行 Agent Loop
        子类可以重写以实现自定义逻辑
        """
        return await self.run_loop(input_data)

    async def run_loop(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """运行 Agent Loop"""
        # 提取session_id（如果有）
        session_id = input_data.get('session_id')
        return await self.loop.run(self, input_data, session_id=session_id)

    # ===== Swarm 协作能力 =====

    def set_capabilities(self, capabilities: List[str]):
        """设置 Agent 的能力标签"""
        self.capabilities = capabilities

    def get_capabilities(self) -> List[str]:
        """获取 Agent 的能力标签"""
        return self.capabilities

    def attach_shared_context(self, shared_context: Any):
        """附加 SharedContext（由 Swarm 调用）"""
        self.shared_context = shared_context

    def attach_identity_manager(self, identity_manager: Any):
        """附加 AgentIdentityManager（由 Swarm 调用）"""
        self.identity_manager = identity_manager

    def set_on_thinking(self, callback):
        """设置 thinking 内容回调"""
        self.loop.on_thinking = callback

    def set_on_tool_step(self, callback):
        """设置工具步骤回调"""
        self.loop.on_tool_step = callback

    def set_on_thinking_done(self, callback):
        """设置推理轮次结束回调"""
        self.loop.on_thinking_done = callback

    def set_on_content_token(self, callback):
        """设置最终回答 token 流式回调"""
        self.loop.on_content_token = callback

    def set_on_questionnaire(self, callback):
        """设置问卷事件回调"""
        self.loop.on_questionnaire = callback

    async def process_subtask(
        self,
        subtask: Any,
        session_id: Optional[str] = None,
        sub_session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理子任务（Swarm 模式）

        子类可以重写以实现自定义逻辑
        默认实现：运行 Agent Loop

        Args:
            sub_session_id: 隔离的子会话 ID，避免 Worker 间历史污染
        """
        # 使用 sub_session_id 进行隔离，若未提供则回退到 session_id（向后兼容）
        effective_session_id = sub_session_id or session_id

        input_data = {
            'question': subtask.description,
            'subtask_id': subtask.id,
            'subtask_type': subtask.type,
            'session_id': effective_session_id,
        }

        return await self.run_loop(input_data)
