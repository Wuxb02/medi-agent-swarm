"""
LangGraph 工具执行节点

替代 AgentLoop 中工具调用的执行逻辑。
不同于 LangGraph 内置 ToolNode（仅支持同步工具），这里：
- 逐一异步执行工具（保持与当前行为一致的串行执行）
- 集成 activate_skill 状态更新（设置 active_skill）
- 集成 ConstraintValidator 约束验证
- 收集知识库 references（doc_id 去重）
- 按当前逻辑计入/不计入 tool_call_count
"""
import json
import time
from typing import Dict, Any, List, Optional, Callable
from loguru import logger

from mediZJ.lgraph.agent_state import AgentState
from mediZJ.lgraph.tool_registry import ToolRegistry

# 约束验证（可选）
try:
    from mediZJ.constraints import ConstraintValidator
    CONSTRAINTS_ENABLED = True
except ImportError:
    CONSTRAINTS_ENABLED = False

# Trace
try:
    from mediZJ.trace.context import traced_span
    from mediZJ.trace.models import SpanType, ToolAttributes
    TRACE_AVAILABLE = True
except ImportError:
    TRACE_AVAILABLE = False


# ---- Tool Call 数据结构（轻量内部表示） ----

# 不消耗 Worker 工具调用配额的系统交互工具（激活技能 / 前端问卷）
_NON_COUNTED_TOOLS = frozenset({"activate_skill"})

class _ToolCall:
    """LLM 返回的单个工具调用"""
    __slots__ = ('id', 'name', 'arguments')

    def __init__(self, id: str, name: str, arguments: Dict[str, Any]):
        self.id = id
        self.name = name
        self.arguments = arguments


def _extract_tool_calls(last_message) -> List[_ToolCall]:
    """从最后一条 assistant 消息中提取 tool_calls（兼容 LangChain AIMessage 和 dict）"""
    # LangChain AIMessage 对象（add_messages reducer 转换后）
    if hasattr(last_message, 'tool_calls') and not isinstance(last_message, dict):
        raw_calls = getattr(last_message, 'tool_calls', []) or []
        result = []
        for tc in raw_calls:
            if isinstance(tc, dict):
                result.append(_ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", ""),
                    arguments=tc.get("args", {}) if "args" in tc else tc.get("arguments", {}),
                ))
            else:
                result.append(_ToolCall(
                    id=getattr(tc, 'id', ''),
                    name=getattr(tc, 'name', ''),
                    arguments=getattr(tc, 'args', {}) if hasattr(tc, 'args') else getattr(tc, 'arguments', {}),
                ))
        return result

    # 普通 dict 格式
    tool_calls_data = last_message.get("tool_calls", []) if isinstance(last_message, dict) else []
    if not tool_calls_data:
        return []

    result = []
    for tc in tool_calls_data:
        func_data = tc.get("function", {})
        name = func_data.get("name", "")
        try:
            args = json.loads(func_data.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}

        result.append(_ToolCall(
            id=tc.get("id", ""),
            name=name,
            arguments=args,
        ))
    return result


# ---- 工具执行节点工厂 ----

def make_tool_execution_node(
    tool_registry: ToolRegistry,
    validator: Optional[Any] = None,
    on_tool_step: Optional[Callable] = None,
):
    """
    创建工具执行节点函数

    这个节点替代 AgentLoop 中以下逻辑：
    - 遍历 llm_response.tool_calls
    - 约束验证 (ConstraintValidator.validate_tool_call)
    - tool_call_count 计数（activate_skill 不计入）
    - agent.execute_tool() → ToolRegistry.execute()
    - 收集 references（doc_id 去重）

    Args:
        tool_registry: 工具注册中心
        validator: ConstraintValidator 实例（可选）
        on_tool_step: 工具步骤回调（可选，用于流式事件）

    Returns:
        async node function: (state: AgentState) -> dict
    """
    _validator = validator
    if _validator is None and CONSTRAINTS_ENABLED:
        from mediZJ.constraints.validator import get_shared_validator
        _validator = get_shared_validator()

    async def tool_execution_node(state: AgentState) -> dict:
        """执行工具调用，返回状态更新"""
        messages = state.get("messages", [])
        if not messages:
            return {}

        last_message = messages[-1]
        tool_calls = _extract_tool_calls(last_message)
        if not tool_calls:
            return {}

        agent_id = state.get("agent_id", "unknown")
        tool_call_count = state.get("tool_call_count", 0)
        collected_refs: Dict[str, Dict] = {
            ref.get("doc_id", str(i)): ref
            for i, ref in enumerate(state.get("references", []))
        }
        active_skill = state.get("active_skill")
        tool_results = []

        for tc in tool_calls:
            # 约束验证
            if _validator:
                validation = _validator.validate_tool_call(agent_id, tc.name)
                if not validation.get("valid"):
                    logger.warning(f"约束警告 [{tc.name}]: {validation.get('reason')}")

            # Trace: TOOL span
            _tool_ctx = traced_span(SpanType.TOOL, name=tc.name) if TRACE_AVAILABLE else None
            if _tool_ctx:
                _tool_ctx.__enter__()

            # 执行工具
            try:
                result = await tool_registry.execute(tc.name, **tc.arguments)
            except Exception as e:
                logger.error(f"工具执行异常 [{tc.name}]: {e}")
                result = {"success": False, "error": str(e), "tool": tc.name}

            if _tool_ctx:
                _tool_ctx.__exit__(None, None, None)

            # 收集知识库引用
            if isinstance(result, dict) and "references" in result:
                for ref in result.get("references", []):
                    doc_id = ref.get("doc_id", "")
                    if doc_id and doc_id not in collected_refs:
                        collected_refs[doc_id] = ref

            # ---- activate_skill 特殊处理 ----
            if tc.name == "activate_skill" and isinstance(result, dict):
                if result.get("success"):
                    # 从 tool_registry 获取 Skill 的指令正文
                    skill_name = tc.arguments.get("name", "")
                    instructions = tool_registry.get_skill_instructions(skill_name)
                    tool_names = tool_registry.get_skill_tool_names(skill_name)

                    # 更新 active_skill（写入 state）
                    active_skill = skill_name

                    # 向 tool_result 注入指令正文（保持 KV cache 优化）
                    result["instructions"] = instructions or ""
                    result["available_tools"] = tool_names
                    result["description"] = (
                        f"Skill '{skill_name}' 已激活，{len(tool_names)} 个工具可用"
                    )

                    logger.info(f"Skill 激活: {skill_name} → {len(tool_names)} 个工具可见")
            elif tc.name not in _NON_COUNTED_TOOLS:
                # activate_skill 不计入 tool_call_count
                tool_call_count += 1

            # 工具步骤回调（流式事件）
            if on_tool_step:
                result_str = str(result)
                on_tool_step(
                    tool_name=tc.name,
                    arguments=tc.arguments,
                    result=result_str[:500],
                    iteration=state.get("iteration", 0),
                    success=("error" not in result_str.lower()),
                )

            # 生成与 assistant.tool_calls 配对的 tool 消息
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

        # 整理引用列表
        reference_list = sorted(collected_refs.values(), key=lambda r: r.get("index", 0))
        for new_idx, ref in enumerate(reference_list, 1):
            ref["index"] = new_idx

        return {
            "messages": tool_results,
            "tool_call_count": tool_call_count,
            "active_skill": active_skill,
            "references": reference_list,
        }

    return tool_execution_node
