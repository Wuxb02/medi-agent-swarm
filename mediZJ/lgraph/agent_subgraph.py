"""
AgentSubGraph — 替代 AgentLoop.run() 的 LangGraph 子图

将 AgentLoop 的 Think-Act-Observe while 循环映射为 LangGraph 状态图：
- prepare_messages → llm_call → 条件路由
  - tools → tool_execution → 回到 llm_call（循环）
  - questionnaire → questionnaire_pause [interrupt] → 回到 llm_call
  - force_answer → force_answer → END
  - done → END

每个 Worker Agent 独立运行一个 AgentSubGraph 实例。
"""
import uuid
import json
import time
import asyncio
from typing import Dict, Any, List, Optional, Callable
from loguru import logger

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.messages import convert_to_openai_messages

from mediZJ.lgraph.agent_state import AgentState
from mediZJ.lgraph.tool_registry import ToolRegistry
from mediZJ.lgraph.tool_executor import make_tool_execution_node, _extract_tool_calls
from mediZJ.core.llm_client import LLMResponse
from mediZJ.core.prompt_loader import PromptLoader

# 约束验证和自动修复
try:
    from mediZJ.constraints import ConstraintValidator
    from mediZJ.validation import AutoFixer
    CONSTRAINTS_ENABLED = True
except ImportError:
    CONSTRAINTS_ENABLED = False


def build_agent_subgraph(
    agent,                          # BaseAgent 实例（持有 LLMClient, SkillRegistry 等）
    tool_registry: ToolRegistry,
    max_iterations: int = 10,
    max_tool_calls: int = 2,
    on_thinking: Optional[Callable] = None,
    on_tool_step: Optional[Callable] = None,
    on_thinking_done: Optional[Callable] = None,
    on_content_token: Optional[Callable] = None,
    on_questionnaire: Optional[Callable] = None,
) -> StateGraph:
    """
    为给定 Agent 构建 LangGraph 子图

    Args:
        agent: BaseAgent 实例
        tool_registry: 工具注册中心
        max_iterations: 最大迭代次数
        max_tool_calls: 最大工具调用次数（activate_skill 不计入）
        on_thinking: thinking 回调
        on_tool_step: 工具步骤回调
        on_thinking_done: 推理轮次结束回调
        on_content_token: 内容 token 回调
        on_questionnaire: 问卷事件回调

    Returns:
        编译后的 CompiledStateGraph
    """
    if CONSTRAINTS_ENABLED:
        from mediZJ.constraints.validator import get_shared_validator
        from mediZJ.validation.auto_fixer import get_shared_auto_fixer
        validator = get_shared_validator()
        auto_fixer = get_shared_auto_fixer()
    else:
        validator = None
        auto_fixer = None

    # 工具执行节点
    _tool_execution_node = make_tool_execution_node(
        tool_registry=tool_registry,
        validator=validator,
        on_tool_step=on_tool_step,
        on_questionnaire=on_questionnaire,
    )

    # ===== 节点函数 =====

    async def _prepare_messages(state: AgentState) -> dict:
        """初始化消息历史（替代 AgentLoop._initialize_messages）"""
        messages = []

        # 系统提示词（稳定版本，不含 Skill 指令，用于 KV cache 前缀）
        system_prompt = agent.get_base_system_prompt_stable()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # 用户档案
        user_context = getattr(agent.loop, 'user_context', None) if hasattr(agent, 'loop') else None
        if user_context:
            messages.append({"role": "system", "content": f"## 用户档案\n{user_context}"})

        # 历史对话（短期记忆）
        session_id = state.get("session_id", "")
        sub_session_id = state.get("sub_session_id", "")
        effective_id = sub_session_id or session_id

        if (effective_id
                and ":" not in effective_id
                and hasattr(agent, 'loop')
                and agent.loop.short_term_memory):
            history = await agent.loop.short_term_memory.get_history(effective_id, limit=5)
            if history:
                logger.info(f"加载 {len(history)} 条历史消息 (session={effective_id})")
                messages.extend(history)

        # 用户输入
        question = state.get("subtask_description") or state.get("question", "")
        user_input = agent.format_user_input({
            "question": question,
            "subtask_id": state.get("subtask_id", ""),
            "subtask_type": state.get("subtask_type", ""),
            "session_id": effective_id,
        })

        messages.append({"role": "user", "content": user_input})

        # 记录用户消息到短期记忆
        if (hasattr(agent, 'loop')
                and agent.loop.short_term_memory
                and session_id):
            await agent.loop.short_term_memory.add_message(
                session_id=effective_id,
                role="user",
                content=user_input,
            )

        logger.info(
            f"[AgentSubGraph] messages prepared: {len(messages)} messages "
            f"(agent={state.get('agent_id', 'unknown')})"
        )

        return {
            "messages": messages,
            "iteration": 0,
            "tool_call_count": 0,
            "force_answer": False,
            "active_skill": None,
            "completed": False,
            "message_count": 1,  # user message counted
        }

    async def _llm_call(state: AgentState) -> dict:
        """LLM 调用节点（替代 AgentLoop 中的 chat_with_tools 调用）"""
        iteration = state.get("iteration", 0) + 1

        # 检查是否达到最大迭代次数
        if iteration > max_iterations:
            logger.warning(f"达到最大迭代次数 {max_iterations}，强制收尾")
            return {
                "iteration": iteration,
                "force_answer": True,
            }

        # 根据 active_skill 过滤可见工具
        active_skill = state.get("active_skill")
        visible_tools = tool_registry.get_visible_tools(active_skill)

        messages = state.get("messages", [])

        # 将 LangChain 消息对象转换为 OpenAI API 格式（add_messages reducer 会将 dict 转为 BaseMessage）
        messages = convert_to_openai_messages(messages)

        # 流式或非流式 LLM 调用
        use_streaming = bool(on_thinking or on_content_token)

        try:
            if use_streaming:
                from mediZJ.core.stream_token_router import StreamTokenRouter

                router = StreamTokenRouter(
                    on_think=lambda token: (
                        on_thinking and on_thinking(content=token, iteration=iteration)
                    ),
                    on_content=lambda token: (
                        on_content_token and on_content_token(token)
                    ),
                )

                llm_response: LLMResponse = await agent.llm_client.chat_with_tools_stream(
                    messages=messages,
                    tools=visible_tools if visible_tools else None,
                    tool_choice="auto",
                    temperature=agent.config.get('temperature', 0.7),
                    on_content_token=router.on_content_token,
                    on_reasoning_token=router.on_reasoning_token,
                    on_tools_detected=router.on_tools_detected,
                )

                if not router.tools_detected:
                    router.flush_content_buffer()
            else:
                llm_response: LLMResponse = await agent.llm_client.chat_with_tools_retry(
                    messages=messages,
                    tools=visible_tools if visible_tools else None,
                    tool_choice="auto",
                    temperature=agent.config.get('temperature', 0.7),
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"LLM 调用异常 (iteration={iteration}): {e}")
            return {
                "iteration": iteration,
                "completed": True,
                "error": str(e),
                "final_answer": "抱歉，系统在处理您的问题时遇到了问题。请稍后重试。",
            }

        # 累加 token 用量
        usage = state.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        if llm_response.usage:
            usage["prompt_tokens"] += llm_response.usage.get("prompt_tokens", 0)
            usage["completion_tokens"] += llm_response.usage.get("completion_tokens", 0)
            usage["total_tokens"] += llm_response.usage.get("total_tokens", 0)

        # Thinking 内容推送
        if on_thinking and llm_response.reasoning_content:
            on_thinking(content=llm_response.reasoning_content, iteration=iteration)

        # 构建 assistant 消息
        assistant_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": llm_response.content or None,
        }

        if llm_response.has_tool_calls():
            # 添加 tool_calls 到消息
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in llm_response.tool_calls
            ]

        # 记录到短期记忆
        if (hasattr(agent, 'loop')
                and agent.loop.short_term_memory
                and state.get("session_id")):
            effective_id = state.get("sub_session_id") or state.get("session_id")
            stm_content = f"调用工具：{', '.join(tc.name for tc in llm_response.tool_calls)}" \
                if llm_response.has_tool_calls() else (llm_response.content or "")
            await agent.loop.short_term_memory.add_message(
                session_id=effective_id,
                role="assistant",
                content=stm_content[:500],
            )

        # Thinking done 回调
        if on_thinking_done and llm_response.has_tool_calls():
            on_thinking_done(iteration=iteration, elapsed_seconds=0)

        msg_count = state.get("message_count", 0) + 1

        return {
            "messages": [assistant_msg],
            "iteration": iteration,
            "usage": usage,
            "message_count": msg_count,
        }

    async def _finalize_answer_node(state: AgentState) -> dict:
        """从最后一条 assistant 消息提取最终回答（正常结束路径）"""
        messages = state.get("messages", [])
        if not messages:
            return {"final_answer": "", "completed": True}

        last_msg = messages[-1]
        # 兼容 LangChain AIMessage 对象和 dict
        if hasattr(last_msg, 'type') and not isinstance(last_msg, dict):
            content = getattr(last_msg, 'content', "") or ""
        else:
            content = last_msg.get("content", "") if isinstance(last_msg, dict) else ""

        iteration = state.get("iteration", 0)
        usage = state.get("usage", {})
        references = state.get("references", [])
        msg_count = state.get("message_count", 0)

        result = {
            "answer": content,
            "iterations": iteration,
            "agent_id": state.get("agent_id", ""),
            "usage": usage,
            "message_count": msg_count,
            "references": references,
        }

        # 后处理
        if hasattr(agent, 'post_process_result'):
            result = await agent.post_process_result(result, content)

        return {
            "final_answer": content,
            "completed": True,
            "message_count": msg_count,
        }

    async def _force_answer_node(state: AgentState) -> dict:
        """强制收尾节点（替代 AgentLoop 中 max_iterations 耗尽时的处理）"""
        messages = state.get("messages", [])

        # 添加强制总结提示
        force_prompt = PromptLoader.render("agent_loop/force_answer.j2")
        messages.append({"role": "user", "content": force_prompt})

        try:
            # 调用 LLM（禁用 tools）
            # 将 LangChain 消息对象转换为 OpenAI API 格式
            openai_messages = convert_to_openai_messages(messages)
            response = await agent.llm_client.chat_with_tools_retry(
                messages=openai_messages,
                tools=None,
                temperature=0.7,
            )

            final_answer = response.content or "抱歉，未能完成任务。"
        except Exception as e:
            logger.error(f"强制收尾 LLM 调用失败: {e}")
            final_answer = "抱歉，系统在处理您的问题时遇到了问题。建议您简化问题或稍后重试。"

        # 记录到短期记忆
        if (hasattr(agent, 'loop')
                and agent.loop.short_term_memory
                and state.get("session_id")):
            effective_id = state.get("sub_session_id") or state.get("session_id")
            await agent.loop.short_term_memory.add_message(
                session_id=effective_id,
                role="assistant",
                content=final_answer,
            )

        msg_count = state.get("message_count", 0) + 1
        usage = state.get("usage", {})
        references = state.get("references", [])
        iteration = state.get("iteration", 0)

        result = {
            "answer": final_answer,
            "iterations": iteration,
            "agent_id": state.get("agent_id", ""),
            "usage": usage,
            "message_count": msg_count,
            "references": references,
            "warning": "max_iterations_reached" if iteration >= max_iterations else "max_tool_calls_reached",
        }

        # 后处理
        if hasattr(agent, 'post_process_result'):
            result = await agent.post_process_result(result, final_answer)

        return {
            "final_answer": final_answer,
            "completed": True,
            "messages": messages,
            "message_count": msg_count,
        }

    async def _questionnaire_pause_node(state: AgentState) -> dict:
        """问卷中断节点：使用 LangGraph interrupt() 挂起等待用户回答"""
        pending = state.get("questionnaire_pending", {})
        if not pending:
            logger.warning("questionnaire_pause 节点被调用但没有 pending 问卷数据")
            return {"questionnaire_pending": None}

        questionnaire_id = pending.get("id", str(uuid.uuid4()))
        questionnaire_data = pending.get("data", {})

        logger.info(f"[AgentSubGraph] 问卷中断: {questionnaire_id}")

        # interrupt() 挂起图执行，返回用户答案
        # 用户答案通过 Command(resume=...) 注入
        user_answer = interrupt({
            "type": "questionnaire",
            "questionnaire_id": questionnaire_id,
            "questionnaire_data": questionnaire_data,
            "source_agent": state.get("agent_id", ""),
        })

        # 格式化用户答案为 LLM 可读文本
        questions_ref = pending.get("_questions_ref", [])
        formatted_text = _format_answers(questions_ref, user_answer)

        logger.info(f"问卷 {questionnaire_id} 收到回答: {formatted_text[:100]}...")

        # 将用户回答作为工具结果追加到消息历史（仅返回新增消息，由 add_messages reducer 合并）
        return {
            "questionnaire_pending": None,
            "questionnaire_answers": user_answer,
            "messages": [{
                "role": "tool",
                "tool_call_id": f"questionnaire_{questionnaire_id}",
                "name": "question_for_user",
                "content": json.dumps({
                    "success": True,
                    "answers": user_answer,
                    "formatted_text": formatted_text,
                }, ensure_ascii=False),
            }],
        }

    # ===== 条件路由函数 =====

    def _route_after_llm(state: AgentState) -> str:
        """LLM 调用后的路由决策（替代 AgentLoop 的 while 循环分支）"""
        if state.get("force_answer"):
            return "force_answer"

        if state.get("completed"):
            return "done"

        messages = state.get("messages", [])
        if not messages:
            return "done"

        last_message = messages[-1]

        # 兼容 LangChain AIMessage 对象（add_messages reducer 转换后）和 dict
        if hasattr(last_message, 'type') and not isinstance(last_message, dict):
            # LangChain AIMessage
            if last_message.type != "ai":
                return "done"
            tool_calls = getattr(last_message, 'tool_calls', []) or []
            # AIMessage.tool_calls 格式: [{"name": "...", "args": {}, "id": "...", "type": "tool_call"}]
            _get_tc_name = lambda tc: tc.get("name", "") if isinstance(tc, dict) else getattr(tc, 'name', '')
        else:
            # dict 格式
            if last_message.get("role") != "assistant":
                return "done"
            tool_calls = last_message.get("tool_calls", [])
            _get_tc_name = lambda tc: tc.get("function", {}).get("name", "")

        if not tool_calls:
            # 无 tool_calls → 最终回答
            return "done"

        # 检查是否达到最大工具调用次数
        non_activate = [tc for tc in tool_calls
                        if _get_tc_name(tc) != "activate_skill"]
        tool_call_count = state.get("tool_call_count", 0)
        if non_activate and tool_call_count >= max_tool_calls:
            logger.warning(f"达到最大工具调用次数 {max_tool_calls}，强制收尾")
            return "force_answer"

        # 检查是否有问卷
        for tc in tool_calls:
            if _get_tc_name(tc) == "question_for_user":
                return "questionnaire"

        return "tool_execution"

    def _route_after_tool(state: AgentState) -> str:
        """工具执行后的路由决策"""
        if state.get("questionnaire_pending"):
            return "questionnaire_pause"

        if state.get("completed"):
            return "done"

        # 检查是否达到最大工具调用次数
        tool_call_count = state.get("tool_call_count", 0)
        iteration = state.get("iteration", 0)
        if tool_call_count >= max_tool_calls or iteration >= max_iterations:
            return "force_answer"

        # 回到 LLM 调用继续循环
        return "llm_call"

    def _route_after_questionnaire(state: AgentState) -> str:
        """问卷回答后的路由"""
        if state.get("completed"):
            return "done"
        return "llm_call"

    # ===== 构建图 =====

    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("prepare_messages", _prepare_messages)
    builder.add_node("llm_call", _llm_call)
    builder.add_node("tool_execution", _tool_execution_node)
    builder.add_node("force_answer", _force_answer_node)
    builder.add_node("finalize_answer", _finalize_answer_node)
    builder.add_node("questionnaire_pause", _questionnaire_pause_node)

    # 边连接
    builder.add_edge(START, "prepare_messages")
    builder.add_edge("prepare_messages", "llm_call")

    # llm_call 后的条件路由
    builder.add_conditional_edges(
        "llm_call",
        _route_after_llm,
        {
            "tool_execution": "tool_execution",
            "questionnaire": "questionnaire_pause",
            "force_answer": "force_answer",
            "done": "finalize_answer",
        }
    )

    # 工具执行后的条件路由
    builder.add_conditional_edges(
        "tool_execution",
        _route_after_tool,
        {
            "questionnaire_pause": "questionnaire_pause",
            "llm_call": "llm_call",
            "force_answer": "force_answer",
            "done": "finalize_answer",
        }
    )

    # 问卷暂停后回到 LLM
    builder.add_conditional_edges(
        "questionnaire_pause",
        _route_after_questionnaire,
        {
            "llm_call": "llm_call",
            "done": "finalize_answer",
        }
    )

    # 正常结束 → END
    builder.add_edge("finalize_answer", END)

    # 强制收尾后结束
    builder.add_edge("force_answer", END)

    # 编译
    return builder.compile(
        checkpointer=MemorySaver(),
    )


# ===== 辅助函数 =====

def _format_answers(questions_ref: List[Dict], answers: Dict[str, Any]) -> str:
    """将用户回答格式化为 LLM 可读文本"""
    if not questions_ref or not answers:
        return json.dumps(answers, ensure_ascii=False) if answers else "（无回答）"

    lines = []
    for q in questions_ref:
        q_id = q.get("id", "")
        q_text = q.get("text", q.get("header", q_id))
        answer = answers.get(q_id, "")
        if isinstance(answer, list):
            answer_str = ", ".join(str(a) for a in answer)
        else:
            answer_str = str(answer) if answer else "（未回答）"
        lines.append(f"- {q_text}: {answer_str}")

    return "\n".join(lines)
