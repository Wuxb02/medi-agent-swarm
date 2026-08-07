"""
Worker 轻量规格：LangGraph Worker 执行所需的全部能力载体

替代旧 Agent 包（BaseAgent / ConsultationAgent / DiagnosticAgent / ResearchAgent）。
Worker 不再是独立 Agent 类，而是承载 AgentSubGraph 执行所需配置与回调的数据载体，
由 SwarmCoordinator 构建，供 supervisor_graph / agent_subgraph 消费。

三个 Worker（consultation / diagnostic / research）能力与旧 Agent 完全一致：
系统提示词、用户输入格式化、结果后处理、LLM 参数均保持原样。
"""
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from loguru import logger

from mediZJ.core.llm_client import LLMClient
from mediZJ.core.prompt_loader import PromptLoader
from mediZJ.core.skill_loader import discover_skills

# Skill 工具加载（全部 Skill 共用一个进程级缓存）
try:
    from mediZJ.core.skill_loader import _discovered_cache as _SKILL_CACHE
except ImportError:  # pragma: no cover
    _SKILL_CACHE = None

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _build_skill_index() -> Dict[str, Any]:
    """一次性构建 Skill 工具索引：{tool_name: {"func": ..., "skill_name": ...}}"""
    if _SKILL_CACHE is not None and _SKILL_CACHE:
        discovered = list(_SKILL_CACHE.values())
    else:
        discovered = discover_skills(_PROJECT_ROOT)
    index: Dict[str, Any] = {}
    for skill_info in discovered:
        skill_name = skill_info["name"]
        for tool_name, func in skill_info.get("tool_functions", {}).items():
            index[tool_name] = {"func": func, "skill_name": skill_name}
    return index


# 模块级单例：所有 Worker 共享，与旧 SkillRegistryMixin 的全量注册等价
_SKILL_INDEX: Dict[str, Any] = _build_skill_index()


def _infer_parameters(func: Callable) -> list:
    """从函数签名推断参数（与旧 SkillRegistryMixin._infer_parameters_from_function 一致）"""
    import inspect
    from mediZJ.core.skill_registry import SkillParameter

    parameters = []
    for param_name, param in inspect.signature(func).parameters.items():
        if param_name in ["self", "args", "kwargs"]:
            continue
        required = param.default == inspect.Parameter.empty
        param_type = "string"
        if any(k in param_name for k in ("count", "limit", "max", "iterations")):
            param_type = "number"
        parameters.append(SkillParameter(
            param_name, param_type, param_name.replace("_", " ").title(), required
        ))
    return parameters


class Worker:
    """Worker 规格：AgentSubGraph 执行的配置与回调载体"""

    def __init__(
        self,
        agent_id: str,
        llm_client: LLMClient,
        system_prompt_template: str,
        user_input_template: str,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.agent_id = agent_id
        self.llm_client = llm_client
        self._system_prompt_template = system_prompt_template
        self._user_input_template = user_input_template
        self.config = {
            "temperature": 0.7,
            "max_iterations": 5,
            **(config or {}),
        }

        # 短期记忆 / 用户档案（由 Coordinator 注入）
        self.short_term_memory: Optional[Any] = None
        self.user_context: Optional[str] = None

        # 流式回调（supervisor_graph 注入，agent_subgraph 消费）
        self.on_thinking: Optional[Callable] = None
        self.on_tool_step: Optional[Callable] = None
        self.on_thinking_done: Optional[Callable] = None
        self.on_content_token: Optional[Callable] = None

    # ===== 回调注入（替代旧 BaseAgent.set_on_*） =====

    def set_on_thinking(self, callback: Optional[Callable]):
        self.on_thinking = callback

    def set_on_tool_step(self, callback: Optional[Callable]):
        self.on_tool_step = callback

    def set_on_thinking_done(self, callback: Optional[Callable]):
        self.on_thinking_done = callback

    def set_on_content_token(self, callback: Optional[Callable]):
        self.on_content_token = callback

    # ===== 系统提示词（稳定版本，不含激活 Skill 指令） =====

    def get_base_system_prompt_stable(self) -> str:
        """系统提示词 + Skill 目录（KV cache 前缀稳定部分）"""
        base = PromptLoader.load(self._system_prompt_template)
        catalog = self._format_skills_catalog()
        if catalog:
            base += (
                f"\n\n---\n## 可用 Skills\n{catalog}\n\n"
                f"使用 activate_skill(name=\"xxx\") 激活技能后方可使用其工具。"
            )
        return base

    def _format_skills_catalog(self) -> str:
        """所有 Skill 的格式化描述（按 Skill 聚合，工具数 + 首行 docstring）"""
        if not _SKILL_INDEX:
            return ""
        skill_names = {}
        for info in _SKILL_INDEX.values():
            skill_names.setdefault(info["skill_name"], []).append(info["func"])
        lines = []
        for name in sorted(skill_names):
            funcs = skill_names[name]
            lines.append(f"- **{name}**: {len(funcs)} 个工具")
        return "\n".join(lines)

    def format_user_input(self, input_data: Dict[str, Any]) -> str:
        """格式化用户输入

        consultation Worker 使用模板渲染（含背景信息块）；
        diagnostic / research Worker 与旧默认实现一致，直接返回问题文本。
        """
        if not self._user_input_template:
            return input_data.get("question") or input_data.get("query") or str(input_data)
        raw_ctx = input_data.get("context", {})
        ctx_text = ""
        if raw_ctx and isinstance(raw_ctx, dict):
            ctx_text = "\n".join(f"{k}: {v}" for k, v in raw_ctx.items())
        return PromptLoader.render(
            self._user_input_template,
            question=input_data.get("question", ""),
            session_id=input_data.get("session_id", ""),
            context=ctx_text,
        )

    async def post_process_result(
        self,
        result: Dict[str, Any],
        final_response: str,
    ) -> Dict[str, Any]:
        """结果后处理（保持旧各 Agent 的行为）"""
        if self.agent_id == "consultation_agent":
            return await self._post_process_consultation(result, final_response)
        if self.agent_id == "diagnostic_agent":
            return await self._post_process_diagnostic(result, final_response)
        if self.agent_id == "research_agent":
            return await self._post_process_research(result, final_response)
        return result

    async def _post_process_consultation(
        self, result: Dict[str, Any], final_response: str
    ) -> Dict[str, Any]:
        """提取【核心建议】并清理【回答】前缀与结构化标签段"""
        import re

        suggestions = []
        match = re.search(r"【核心建议】\s*\n((?:\d+\.\s*.+\n?)+)", final_response)
        if match:
            suggestion_lines = re.findall(r"\d+\.\s*(.+)", match.group(1))
            suggestions = [s.strip() for s in suggestion_lines if s.strip()]

        clean_answer = re.sub(r"^【回答】\s*\n?", "", final_response)
        clean_answer = re.sub(r"\n?【核心建议】[\s\S]*$", "", clean_answer)
        clean_answer = clean_answer.strip()

        result.update({"answer": clean_answer, "suggestions": suggestions[:5]})
        return result

    async def _post_process_diagnostic(
        self, result: Dict[str, Any], final_response: str
    ) -> Dict[str, Any]:
        """提取风险等级"""
        risk_level = "unknown"
        if "风险等级" in final_response:
            if "高" in final_response or "HIGH" in final_response:
                risk_level = "high"
            elif "中" in final_response or "MEDIUM" in final_response:
                risk_level = "medium"
            elif "低" in final_response or "LOW" in final_response:
                risk_level = "low"
        result.update({"risk_level": risk_level, "diagnosis_provided": True})
        return result

    async def _post_process_research(
        self, result: Dict[str, Any], final_response: str
    ) -> Dict[str, Any]:
        """识别证据等级并统计文献数量"""
        evidence_level = "unknown"
        if "A级" in final_response or "A 级" in final_response:
            evidence_level = "A"
        elif "B级" in final_response or "B 级" in final_response:
            evidence_level = "B"
        elif "C级" in final_response or "C 级" in final_response:
            evidence_level = "C"
        result.update({
            "evidence_level": evidence_level,
            "literature_count": final_response.count("文献"),
            "evidence_provided": True,
        })
        return result

    # ===== Skill 工具执行（替代旧 SkillRegistry.execute） =====

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """按工具名查找并执行 Skill 工具函数"""
        import asyncio

        info = _SKILL_INDEX.get(tool_name)
        if info is None:
            logger.error(f"Tool not found: {tool_name}")
            return {"success": False, "error": f"Tool not found: {tool_name}"}
        try:
            func = info["func"]
            if asyncio.iscoroutinefunction(func):
                return await func(**arguments)
            return await asyncio.get_event_loop().run_in_executor(
                None, lambda: func(**arguments)
            )
        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name} - {e}")
            return {"success": False, "error": str(e), "tool": tool_name}

    def to_openai_format(self) -> list:
        """全部 Skill 工具（OpenAI function calling 格式），供兼容层使用"""
        return [_build_tool_schema(tool_name, info) for tool_name, info in _SKILL_INDEX.items()]


def _build_tool_schema(tool_name: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """构建单个工具的 OpenAI schema"""
    func = info["func"]
    description = (func.__doc__ or "").strip().split("\n")[0] or f"工具: {tool_name}"
    params = _infer_parameters(func)
    properties, required = {}, []
    for param in params:
        prop = {"type": param.type, "description": param.description}
        if param.enum:
            prop["enum"] = param.enum
        properties[param.name] = prop
        if param.required:
            required.append(param.name)
    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def create_worker(
    agent_id: str,
    llm_client: Optional[LLMClient] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Worker:
    """按 agent_id 创建对应 Worker（consultation / diagnostic / research）"""
    spec = {
        "consultation_agent": {
            "system_prompt_template": "agents/consultation_system.j2",
            "user_input_template": "agents/consultation_user_input.j2",
        },
        "diagnostic_agent": {
            "system_prompt_template": "agents/diagnostic_system.j2",
            "user_input_template": None,
        },
        "research_agent": {
            "system_prompt_template": "agents/research_system.j2",
            "user_input_template": None,
        },
    }
    if agent_id not in spec:
        raise ValueError(f"未知 Worker agent_id: {agent_id}")

    default_config = {
        "max_iterations": 5,
        "temperature": {"consultation_agent": 0.8}.get(agent_id, 0.7),
    }
    return Worker(
        agent_id=agent_id,
        llm_client=llm_client or LLMClient(),
        system_prompt_template=spec[agent_id]["system_prompt_template"],
        user_input_template=spec[agent_id]["user_input_template"],
        config={**default_config, **(config or {})},
    )
