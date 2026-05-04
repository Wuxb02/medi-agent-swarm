"""
基础工具定义
始终可用的系统级工具，不依赖任何 Skill
"""
from typing import Dict, Any


def create_activate_skill_tool(registry) -> callable:
    """
    创建 activate_skill 工具函数（闭包，捕获 registry 引用）

    Args:
        registry: SkillRegistry 实例

    Returns:
        activate_skill 函数
    """

    async def activate_skill(name: str) -> Dict[str, Any]:
        """
        激活指定 Skill。激活后可以使用该 Skill 的工具。
        同一时间只能有一个 Skill 处于激活状态，激活新 Skill 会自动停用之前的。

        Args:
            name: Skill 名称（如 "search-knowledge", "deep-research"）

        Returns:
            激活结果，包含 Skill 描述和可用工具列表
        """
        # 检查 compat_mode
        if registry.compat_mode:
            return {
                "success": False,
                "error": "当前为兼容模式，所有工具已直接可用，无需激活 Skill"
            }

        skill_name, instructions = registry.activate_skill(name)

        if skill_name is None:
            # 列出可用的 Skills
            available = list(registry.get_all_skill_definitions().keys())
            return {
                "success": False,
                "error": f"未找到 Skill: {name}",
                "available_skills": available
            }

        skill_def = registry.get_skill_definition(skill_name)
        return {
            "success": True,
            "skill": skill_name,
            "description": skill_def.description,
            "available_tools": skill_def.tool_names,
            "message": f"已激活 Skill: {skill_name}，现在可以使用以下工具: {', '.join(skill_def.tool_names)}"
        }

    return activate_skill
