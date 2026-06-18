"""
Skill 数据模型
定义 SkillDefinition 数据类，封装 Skill 的元数据、指令正文和工具函数
"""
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

from mediZJ.core.skill_registry import SkillParameter


@dataclass
class SkillDefinition:
    """
    Skill 定义
    一个 Skill 是一个能力包，包含指令正文和可调用的工具函数
    """
    name: str                                    # Skill 名称（如 "deep-research"）
    description: str                             # Skill 描述（来自 YAML frontmatter）
    instructions: str                            # Skill 指令正文（SKILL.md body）
    tool_names: List[str] = field(default_factory=list)  # 声明的工具函数名列表
    tool_functions: Dict[str, Callable] = field(default_factory=dict)  # 函数名 -> 函数对象
    tool_parameters: Dict[str, List[SkillParameter]] = field(default_factory=dict)  # 函数名 -> 参数列表
    migrated: bool = False                       # 是否已迁移到新格式（有 tools 声明）

    def get_tool_descriptions(self) -> str:
        """获取工具的格式化描述"""
        if not self.tool_names:
            return "（无可用工具）"
        lines = []
        for name in self.tool_names:
            func = self.tool_functions.get(name)
            if func and func.__doc__:
                doc_first_line = func.__doc__.strip().split('\n')[0]
                lines.append(f"  - {name}: {doc_first_line}")
            else:
                lines.append(f"  - {name}")
        return '\n'.join(lines)
