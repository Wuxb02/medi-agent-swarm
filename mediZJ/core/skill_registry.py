"""
Skill 注册系统
支持双层架构：
  - Skill 层：能力包（指令正文 + 工具函数）
  - Tool 层：底层可调用函数
支持兼容模式（平铺所有工具）和双层模式（base tools + 激活的 skill tools）
"""
from typing import Dict, Any, List, Callable, Optional, Tuple
from dataclasses import dataclass
import inspect
import asyncio
from loguru import logger


@dataclass
class SkillParameter:
    """Skill 参数定义"""
    name: str
    type: str  # "string", "number", "integer", "boolean", "object", "array"
    description: str
    required: bool = False
    enum: Optional[List[str]] = None


class SkillRegistry:
    """
    Skill 注册表（双层架构）

    支持两种模式：
    - compat_mode=True：兼容模式，所有 Skill 作为平铺工具暴露（旧行为）
    - compat_mode=False：双层模式，base_tools + 当前激活 Skill 的 tools
    """

    def __init__(self):
        # === Skill 层 ===
        # 所有注册的 Skill 定义（通过 register_skill 注册）
        # 延迟导入避免循环引用
        self._skill_definitions: Dict[str, Any] = {}  # name -> SkillDefinition

        # === Tool 层 ===
        # 始终可用的工具（通过 register_base_tool 注册）
        self.base_tools: Dict[str, Dict[str, Any]] = {}

        # 兼容模式：旧的平铺注册（通过 register 注册）
        self.skills: Dict[str, Dict[str, Any]] = {}

        # === 状态 ===
        self.active_skill: Optional[str] = None  # 当前激活的 Skill 名称
        self.compat_mode = True  # 兼容模式默认开启

    # =====================================================
    # 新的双层注册方法
    # =====================================================

    def register_skill(self, skill_def):
        """
        注册一个完整的 Skill 定义

        Args:
            skill_def: SkillDefinition 对象
        """
        self._skill_definitions[skill_def.name] = skill_def
        logger.debug(f"Registered skill definition: {skill_def.name} "
                     f"(tools={skill_def.tool_names}, migrated={skill_def.migrated})")

    def register_base_tool(
        self,
        name: str,
        function: Callable,
        description: str,
        parameters: List[SkillParameter]
    ):
        """
        注册一个始终可用的基础工具

        Args:
            name: 工具名称
            function: 工具函数
            description: 工具描述
            parameters: 参数列表
        """
        self.base_tools[name] = {
            'function': function,
            'description': description,
            'parameters': parameters,
            'is_async': inspect.iscoroutinefunction(function)
        }
        logger.debug(f"Registered base tool: {name}")

    def activate_skill(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        激活指定 Skill，自动停用当前 Skill

        Args:
            name: Skill 名称

        Returns:
            (skill_name, instructions) 或 (None, None) 如果未找到
        """
        if name not in self._skill_definitions:
            logger.warning(f"Skill not found for activation: {name}")
            return None, None

        # 停用当前 Skill
        if self.active_skill and self.active_skill != name:
            logger.debug(f"Deactivating skill: {self.active_skill}")

        self.active_skill = name
        skill_def = self._skill_definitions[name]
        logger.info(f"🎯 Activated skill: {name}")
        return name, skill_def.instructions

    def get_active_instructions(self) -> Optional[str]:
        """获取当前激活 Skill 的指令正文"""
        if not self.active_skill:
            return None
        skill_def = self._skill_definitions.get(self.active_skill)
        if skill_def:
            return skill_def.instructions
        return None

    def get_active_skill_name(self) -> Optional[str]:
        """获取当前激活 Skill 的名称"""
        return self.active_skill

    def get_skills_catalog(self) -> str:
        """
        返回所有 Skill 的格式化描述（用于写入 system prompt）

        Returns:
            格式化的 Skill 列表文本
        """
        if not self._skill_definitions:
            return "（无可用 Skills）"

        lines = []
        for name, skill_def in self._skill_definitions.items():
            desc = skill_def.description
            tool_count = len(skill_def.tool_names)
            lines.append(f"- **{name}**: {desc}（{tool_count}个工具）")
        return '\n'.join(lines)

    def get_skill_definition(self, name: str):
        """获取 Skill 定义"""
        return self._skill_definitions.get(name)

    def get_all_skill_definitions(self) -> Dict[str, Any]:
        """获取所有 Skill 定义"""
        return self._skill_definitions

    def has_migrated_skills(self) -> bool:
        """检查是否所有 Skill 都已迁移到新格式"""
        if not self._skill_definitions:
            return False
        return all(sd.migrated for sd in self._skill_definitions.values())

    # =====================================================
    # 兼容旧接口（保持向后兼容）
    # =====================================================

    def register(
        self,
        name: str,
        function: Callable,
        description: str,
        parameters: List[SkillParameter]
    ):
        """
        兼容旧接口：平铺注册一个 Skill 函数为工具
        """
        self.skills[name] = {
            'function': function,
            'description': description,
            'parameters': parameters,
            'is_async': inspect.iscoroutinefunction(function)
        }
        logger.debug(f"Registered skill (legacy): {name}")

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """获取 Skill（兼容旧接口）"""
        return self.skills.get(name)

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 Skills（兼容旧接口）"""
        return self.skills

    async def execute(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """
        执行工具（支持 base_tools、active skill tools 和兼容模式的平铺 tools）
        """
        # 优先查找 base_tools
        if tool_name in self.base_tools:
            tool = self.base_tools[tool_name]
            return await self._execute_tool(tool_name, tool, kwargs)

        # 双层模式：查找当前激活 Skill 的工具
        if not self.compat_mode and self.active_skill:
            skill_def = self._skill_definitions.get(self.active_skill)
            if skill_def and tool_name in skill_def.tool_functions:
                func = skill_def.tool_functions[tool_name]
                is_async = inspect.iscoroutinefunction(func)
                tool = {'function': func, 'is_async': is_async}
                return await self._execute_tool(tool_name, tool, kwargs)

        # 兼容模式：查找平铺注册的 skills
        if tool_name in self.skills:
            return await self._execute_tool(tool_name, self.skills[tool_name], kwargs)

        error_msg = f"Tool not found: {tool_name}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg}

    async def _execute_tool(self, name: str, tool: Dict, kwargs: Dict) -> Dict[str, Any]:
        """执行工具的通用逻辑"""
        try:
            logger.debug(f"Executing tool: {name} with args: {kwargs}")

            if tool['is_async']:
                result = await tool['function'](**kwargs)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: tool['function'](**kwargs)
                )

            logger.debug(f"Tool {name} completed successfully")
            return result

        except Exception as e:
            error_msg = f"Tool execution failed: {name} - {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "tool": name
            }

    def to_openai_format(self) -> List[Dict[str, Any]]:
        """
        转换为 OpenAI function calling 格式

        兼容模式：返回所有平铺注册的 skills
        双层模式：返回 base_tools + 当前激活 Skill 的 tools
        """
        if self.compat_mode:
            return self._to_openai_format_compat()
        else:
            return self._to_openai_format_layered()

    def _to_openai_format_compat(self) -> List[Dict[str, Any]]:
        """兼容模式：返回所有平铺注册的 skills"""
        tools = []
        for name, skill in self.skills.items():
            tools.append(self._build_tool_schema(name, skill['description'], skill['parameters']))
        return tools

    def _to_openai_format_layered(self) -> List[Dict[str, Any]]:
        """双层模式：返回 base_tools + 当前激活 Skill 的 tools"""
        tools = []

        # 1. 始终包含 base_tools
        for name, tool in self.base_tools.items():
            tools.append(self._build_tool_schema(name, tool['description'], tool['parameters']))

        # 2. 如果有激活的 Skill，包含其工具
        if self.active_skill:
            skill_def = self._skill_definitions.get(self.active_skill)
            if skill_def:
                for tool_name in skill_def.tool_names:
                    params = skill_def.tool_parameters.get(tool_name, [])
                    # 从函数 docstring 获取描述
                    func = skill_def.tool_functions.get(tool_name)
                    desc = ""
                    if func and func.__doc__:
                        desc = func.__doc__.strip().split('\n')[0]
                    tools.append(self._build_tool_schema(tool_name, desc, params))

        return tools

    def _build_tool_schema(self, name: str, description: str, parameters: List[SkillParameter]) -> Dict[str, Any]:
        """构建单个工具的 OpenAI schema"""
        properties = {}
        required = []

        for param in parameters:
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
                'name': name,
                'description': description,
                'parameters': {
                    'type': 'object',
                    'properties': properties,
                    'required': required
                }
            }
        }

    # =====================================================
    # 兼容模式切换
    # =====================================================

    def set_compat_mode(self, enabled: bool):
        """设置兼容模式"""
        self.compat_mode = enabled
        if enabled:
            self.active_skill = None
        logger.info(f"SkillRegistry compat_mode = {enabled}")

    def to_openai_format_all(self) -> List[Dict[str, Any]]:
        """返回所有注册工具（不论模式），用于调试"""
        return self._to_openai_format_compat()

    # =====================================================
    # LangGraph 迁移接口
    # =====================================================

    def export_for_langgraph(self) -> Dict[str, Any]:
        """
        导出 Skill 数据供 LangGraph ToolRegistry 使用

        Returns:
            {
                "skill_definitions": {name: SkillDefinition},
                "base_tools": {name: {function, description, parameters}},
            }
        """
        return {
            "skill_definitions": dict(self._skill_definitions),
            "base_tools": dict(self.base_tools),
        }
