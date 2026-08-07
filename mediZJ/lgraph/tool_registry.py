"""
LangGraph 工具注册中心

替代 SkillRegistry 的 to_openai_format() 和 execute() 职责。
采用可见性标记模型：所有工具预注册，根据 active_skill 过滤可见工具。

核心变更：
- 不再动态重建 OpenAI schema，改为预注册 + 可见性过滤
- activate_skill 仅修改 state.active_skill，由 LLM 节点绑定时过滤
- 保留工具函数的原始签名和调用方式
"""
import inspect
import asyncio
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass, field
from loguru import logger

from mediZJ.core.skill_registry import SkillParameter


@dataclass
class VisibleTool:
    """带可见性标记的工具

    可见性规则：
    - visible_in = ["*"]  → 始终可见（base_tools: activate_skill, question_for_user）
    - visible_in = ["skill-name"] → 仅当 active_skill == "skill-name" 时可见
    """
    func: Callable                          # 原始 async 函数
    name: str                               # 工具名称
    description: str                        # 工具描述
    parameters: List[SkillParameter]        # 参数定义
    visible_in: List[str]                   # 可见性分组
    skill_instructions: Optional[str] = None  # SKILL.md body（仅 Skill 工具有）
    allowed_agents: Optional[List[str]] = None  # 允许调用的 Agent ID 列表（None 表示不限制）

    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling 格式"""
        properties = {}
        required = []

        for param in self.parameters:
            prop = {
                'type': param.type,
                'description': param.description
            }
            if param.enum:
                prop['enum'] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            'type': 'function',
            'function': {
                'name': self.name,
                'description': self.description,
                'parameters': {
                    'type': 'object',
                    'properties': properties,
                    'required': required,
                }
            }
        }


class ToolRegistry:
    """
    LangGraph 工具注册中心

    管理所有工具的注册、可见性过滤和执行。

    使用方式：
        registry = ToolRegistry()
        registry.register_from_skills(project_root)  # 从 .claude/skills/ 批量注册
        registry.register_base_tools(...)            # 注册始终可见的基础工具

        # 在 LLM 节点中
        visible = registry.get_visible_tools(state.get("active_skill"))

        # 在工具执行节点中
        result = await registry.execute(tool_name, **args)
    """

    def __init__(self):
        # name -> VisibleTool
        self._tools: Dict[str, VisibleTool] = {}

    # ===== 注册方法 =====

    def register(self, tool: VisibleTool):
        """注册单个工具"""
        self._tools[tool.name] = tool
        logger.debug(f"[ToolRegistry] registered: {tool.name} (visible_in={tool.visible_in})")

    def register_from_skills(self, discovered_skills: List[Dict],
                             tool_params: Optional[Dict[str, List[SkillParameter]]] = None):
        """
        从 discover_skills() 的结果批量注册 Skill 工具

        Args:
            discovered_skills: skill_loader.discover_skills() 的返回值
            tool_params: {tool_name: [SkillParameter]} 参数映射，
                         如果不提供，则从函数签名自动推断
        """
        for skill_info in discovered_skills:
            skill_name = skill_info["name"]
            metadata = skill_info["metadata"]
            tool_functions = skill_info.get("tool_functions", {})

            for tool_name, func in tool_functions.items():
                # 获取参数定义
                params = []
                if tool_params and tool_name in tool_params:
                    params = tool_params[tool_name]
                else:
                    params = self._infer_parameters(func)

                tool = VisibleTool(
                    func=func,
                    name=tool_name,
                    description=metadata.get("description", ""),
                    parameters=params,
                    visible_in=[skill_name],
                    skill_instructions=metadata.get("instructions", ""),
                )
                self.register(tool)

            logger.info(
                f"[ToolRegistry] loaded skill '{skill_name}': "
                f"{len(tool_functions)} tool(s) — {list(tool_functions.keys())}"
            )

    def register_base_tool(self, name: str, func: Callable, description: str,
                           parameters: Optional[List[SkillParameter]] = None,
                           allowed_agents: Optional[List[str]] = None):
        """注册始终可见的基础工具（visible_in=["*"]）

        Args:
            allowed_agents: 允许调用的 Agent ID 列表（None 表示所有 Agent 可见）
        """
        if parameters is None:
            parameters = self._infer_parameters(func)

        tool = VisibleTool(
            func=func,
            name=name,
            description=description,
            parameters=parameters,
            visible_in=["*"],
            allowed_agents=allowed_agents,
        )
        self.register(tool)

    # ===== 可见性过滤 =====

    def get_visible_tools(self, active_skill: Optional[str] = None,
                          agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        根据当前激活的 Skill 与调用者身份过滤可见工具

        规则：
        - visible_in = ["*"] → 始终可见；若设置了 allowed_agents，还需 agent_id 在其内
        - visible_in 包含 active_skill → 可见
        - 其他 → 不可见

        Args:
            active_skill: 当前激活的 Skill 名称（None 表示未激活）
            agent_id: 调用者 Agent ID（用于基础工具权限收口，如 question_for_user 仅 LeadAgent）

        Returns:
            OpenAI function calling 格式的工具列表
        """
        visible = []
        for tool in self._tools.values():
            if "*" in tool.visible_in:
                if (tool.allowed_agents is None
                        or agent_id in tool.allowed_agents):
                    visible.append(tool.to_openai_schema())
            elif active_skill and active_skill in tool.visible_in:
                visible.append(tool.to_openai_schema())

        logger.debug(
            f"[ToolRegistry] visible tools: {len(visible)} "
            f"(total={len(self._tools)}, active_skill={active_skill}, agent_id={agent_id})"
        )
        return visible

    def get_skill_names(self) -> List[str]:
        """获取所有可激活的 Skill 名称列表"""
        names = set()
        for tool in self._tools.values():
            for grp in tool.visible_in:
                if grp != "*":
                    names.add(grp)
        return sorted(names)

    def get_skill_instructions(self, skill_name: str) -> Optional[str]:
        """获取指定 Skill 的指令正文"""
        for tool in self._tools.values():
            if skill_name in tool.visible_in and tool.skill_instructions:
                return tool.skill_instructions
        return None

    def get_skill_tool_names(self, skill_name: str) -> List[str]:
        """获取指定 Skill 的所有工具名称"""
        names = []
        for tool in self._tools.values():
            if skill_name in tool.visible_in:
                names.append(tool.name)
        return names

    # ===== 工具执行 =====

    async def execute(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """
        执行工具（兼容当前 SkillRegistry.execute() 接口）

        Args:
            tool_name: 工具名称
            **kwargs: 工具参数

        Returns:
            工具执行结果字典
        """
        tool = self._tools.get(tool_name)
        if not tool:
            error_msg = f"Tool not found: {tool_name}"
            logger.error(f"[ToolRegistry] {error_msg}")
            return {"success": False, "error": error_msg}

        try:
            logger.debug(f"[ToolRegistry] executing: {tool_name}({kwargs})")

            if inspect.iscoroutinefunction(tool.func):
                result = await tool.func(**kwargs)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: tool.func(**kwargs))

            logger.debug(f"[ToolRegistry] {tool_name} completed")
            return result

        except Exception as e:
            error_msg = f"Tool execution failed: {tool_name} - {str(e)}"
            logger.error(f"[ToolRegistry] {error_msg}")
            return {"success": False, "error": error_msg, "tool": tool_name}

    def get(self, tool_name: str) -> Optional[VisibleTool]:
        """获取工具定义"""
        return self._tools.get(tool_name)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, tool_name: str) -> bool:
        return tool_name in self._tools

    # ===== 辅助方法 =====

    @staticmethod
    def _infer_parameters(func: Callable) -> List[SkillParameter]:
        """从函数签名推断参数定义"""
        params = []
        try:
            sig = inspect.signature(func)
            for pname, param in sig.parameters.items():
                if pname in ('self', 'cls'):
                    continue

                # 推断类型
                param_type = "string"
                if pname in ('count', 'limit', 'max_results', 'max_iterations',
                             'max_tokens', 'top_k', 'top_n'):
                    param_type = "number"

                required = param.default is inspect.Parameter.empty

                # 从 docstring 提取参数描述
                desc = f"参数: {pname}"
                if func.__doc__:
                    for line in func.__doc__.split('\n'):
                        line = line.strip()
                        if line.startswith(f':param {pname}:'):
                            desc = line.split(':', 2)[-1].strip()
                            break

                params.append(SkillParameter(
                    name=pname,
                    type=param_type,
                    description=desc,
                    required=required,
                ))
        except Exception:
            pass
        return params
