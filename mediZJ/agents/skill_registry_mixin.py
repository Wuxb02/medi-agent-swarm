"""
统一的 Skills 注册（自动发现）
所有 Worker Agents 共享
支持双层架构：register_skill() 注册完整 Skill 定义
"""
from mediZJ.core.skill_loader import discover_skills
from mediZJ.core.skill_registry import SkillParameter
from mediZJ.core.skill_models import SkillDefinition
from pathlib import Path
from loguru import logger
import inspect


class SkillRegistryMixin:
    """
    统一注册所有 Skills（自动发现模式）

    所有 Worker Agents (ConsultationAgent, DiagnosticAgent, ResearchAgent)
    都继承这个 Mixin，避免重复代码
    """

    def register_all_skills(self):
        """
        自动扫描并注册所有 Skills

        Skills 会从 .claude/skills/ 目录自动发现，
        无需手动维护列表
        """
        project_root = Path(__file__).parent.parent.parent
        discovered = discover_skills(project_root)

        # 自动注册所有发现的 skills
        for skill_info in discovered:
            metadata = skill_info["metadata"]
            tool_functions = skill_info.get("tool_functions", {})
            migrated = skill_info.get("migrated", False)

            # 为每个工具函数推断参数
            tool_parameters = {}
            for func_name, func in tool_functions.items():
                params = self._infer_parameters_from_function(func)
                tool_parameters[func_name] = params

            # 构建 SkillDefinition
            skill_def = SkillDefinition(
                name=skill_info["name"],
                description=metadata.get("description", f"Skill: {skill_info['name']}"),
                instructions=metadata.get("instructions", ""),
                tool_names=list(tool_functions.keys()),
                tool_functions=tool_functions,
                tool_parameters=tool_parameters,
                migrated=migrated
            )

            # 注册到 SkillRegistry（双层架构）
            self.skill_registry.register_skill(skill_def)

            # 兼容旧接口：同时平铺注册主函数（compat_mode 时使用）
            if skill_info.get("function"):
                function_name = skill_info["function_name"]
                func = skill_info["function"]
                description = metadata.get("description", f"Skill: {skill_info['name']}")
                parameters = self._infer_parameters_from_function(func)

                self.skill_registry.register(
                    name=function_name,
                    function=func,
                    description=description,
                    parameters=parameters
                )

            logger.info(f"✅ Registered skill: {skill_info['name']} "
                        f"(tools={list(tool_functions.keys())}, migrated={migrated})")

        logger.info(f"Total {len(discovered)} skills registered")

    def _infer_parameters_from_function(self, func) -> list:
        """
        从函数签名推断参数

        Args:
            func: Python 函数对象

        Returns:
            [SkillParameter(...), ...]
        """
        sig = inspect.signature(func)
        parameters = []

        for param_name, param in sig.parameters.items():
            # 跳过 self 和特殊参数
            if param_name in ["self", "args", "kwargs"]:
                continue

            # 判断是否必需
            required = param.default == inspect.Parameter.empty

            # 推断类型（简单规则）
            param_type = "string"
            if "count" in param_name or "limit" in param_name or "max" in param_name or "iterations" in param_name:
                param_type = "number"

            # 生成描述
            param_desc = param_name.replace('_', ' ').title()

            parameters.append(SkillParameter(
                param_name,
                param_type,
                param_desc,
                required
            ))

        return parameters
