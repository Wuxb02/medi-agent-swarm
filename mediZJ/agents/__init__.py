"""
Agents模块
"""
from .base_agent import BaseAgent
from .consultation_agent import ConsultationAgent, consult
from .diagnostic_agent import DiagnosticAgent, diagnose
from .research_agent import ResearchAgent, research

__all__ = [
    'BaseAgent',
    'ConsultationAgent',
    'consult',
    'DiagnosticAgent',
    'diagnose',
    'ResearchAgent',
    'research',
]
