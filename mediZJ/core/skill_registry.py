"""
Skill 参数数据模型

LangGraph 迁移后，SkillRegistry 类已被 ToolRegistry（mediZJ/lgraph/tool_registry.py）
取代。此处仅保留 SkillParameter 数据类，供 ToolRegistry 与 Skill 加载层复用。
"""
from typing import List, Optional
from dataclasses import dataclass


@dataclass
class SkillParameter:
    """Skill 参数定义"""
    name: str
    type: str  # "string", "number", "integer", "boolean", "object", "array"
    description: str
    required: bool = False
    enum: Optional[List[str]] = None
