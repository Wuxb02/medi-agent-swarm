"""
core.tools — 统一工具包
LangGraph 迁移后，activate_skill 由 ToolRegistry 内联实现，此处仅保留问卷工具。
"""
from .questionnaire import create_question_for_user_tool

__all__ = ['create_question_for_user_tool']
