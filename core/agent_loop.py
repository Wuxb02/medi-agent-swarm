"""
Agent循环引擎
实现 LLM 驱动的 Skill 调用循环
支持短期记忆集成
支持约束验证（Harness Engineering）
"""
import uuid
import json
import time
from typing import Dict, Any, List, Optional
from loguru import logger

from .state_manager import StateManager, TaskStatus
from .llm_client import LLMResponse
from .prompt_loader import PromptLoader

# Harness Engineering: 约束验证和自动修复
try:
    from constraints import ConstraintValidator
    from validation import AutoFixer
    CONSTRAINTS_ENABLED = True
except ImportError:
    logger.warning("Constraints module not found, running without constraint validation")
    CONSTRAINTS_ENABLED = False


class AgentLoop:
    """
    Agent循环引擎
    LLM 自主决策 Skill 调用，循环直到任务完成

    功能：
    - 支持短期记忆（ShortTermMemory）
    - 自动记录每轮的 user/assistant 消息
    """

    def __init__(self, max_iterations: int = 10, short_term_memory: Optional[Any] = None, max_tool_calls: int = 2,
                 on_thinking: Optional[Any] = None, on_tool_step: Optional[Any] = None,
                 on_thinking_done: Optional[Any] = None, on_content_token: Optional[Any] = None):
        """
        初始化Agent循环引擎

        Args:
            max_iterations: 最大迭代次数（防止无限循环）
            short_term_memory: 短期记忆管理器（可选）
            max_tool_calls: 最大 Skill 调用次数（硬性限制，默认2次）
            on_thinking: thinking 内容回调（可选）
            on_tool_step: 工具步骤回调（可选）
            on_thinking_done: 推理轮次结束回调（可选）
            on_content_token: 最终回答 token 流式回调（可选）
        """
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.state_manager = StateManager()
        self.short_term_memory = short_term_memory
        self.tool_call_count = 0
        self.on_thinking = on_thinking
        self.on_tool_step = on_tool_step
        self.on_thinking_done = on_thinking_done
        self.on_content_token = on_content_token

        # Harness Engineering: 约束验证器和自动修复器
        self.validator = ConstraintValidator() if CONSTRAINTS_ENABLED else None
        self.auto_fixer = AutoFixer() if CONSTRAINTS_ENABLED else None
        if CONSTRAINTS_ENABLED:
            logger.debug("✅ Constraint validation enabled")

    async def run(self, agent, input_data: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        执行Agent循环

        Args:
            agent: Agent实例
            input_data: 输入数据

        Returns:
            最终结果
        """
        task_id = str(uuid.uuid4())
        state = self.state_manager.create_state(
            task_id=task_id,
            agent_id=agent.agent_id,
            input_data=input_data,
            max_iterations=self.max_iterations
        )

        # 重置计数
        self.tool_call_count = 0

        # 重置 Skill 激活状态（每次 loop 开始时无激活的 Skill）
        if hasattr(agent, 'skill_registry') and agent.skill_registry:
            agent.skill_registry.active_skill = None

        # Token 用量累加器
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0

        # 消息数计数器
        message_count = 0

        logger.info(f"Starting Agent Loop for {agent.agent_id}, task_id={task_id}")

        try:
            state.status = TaskStatus.IN_PROGRESS

            # 初始化消息历史（包含历史对话）
            messages = await self._initialize_messages(agent, input_data, session_id)

            # 记录用户消息到短期记忆
            if self.short_term_memory and session_id:
                user_message = messages[-1]["content"] if messages else str(input_data)
                self.short_term_memory.add_message(
                    session_id=session_id,
                    role="user",
                    content=user_message
                )
                message_count += 1
                logger.debug(f"Recorded user message to short-term memory (session={session_id})")

            # 获取 Agent 的 Skills (OpenAI format)
            tools_openai_format = agent.get_tools_for_llm()

            logger.debug(f"Agent has {len(tools_openai_format) if tools_openai_format else 0} skills available")

            # 主循环：LLM → Skill Calls → Results → LLM
            while state.should_continue():
                state.iteration += 1
                logger.debug(f"=== Iteration {state.iteration}/{state.max_iterations} ===")

                try:
                    # 调用 LLM（流式或非流式）
                    use_streaming = bool(self.on_thinking or self.on_content_token)

                    if use_streaming:
                        # 流式模式：逐 token 回调，动态路由到 thinking 或 content
                        _has_tools = [False]  # 用 list 以在闭包中可变
                        _reasoning_active = [False]  # 推理内容是否正在输出
                        _content_buffer: List[str] = []  # 推理期间缓存的 content token

                        def _flush_content_buffer():
                            """推理结束后，将缓存的 content token 推送到正文"""
                            if _content_buffer and self.on_content_token:
                                for token in _content_buffer:
                                    self.on_content_token(token)
                                _content_buffer.clear()

                        def _route_token(token: str):
                            if _has_tools[0]:
                                # 已检测到 tool_calls → token 是 thinking 内容
                                if self.on_thinking:
                                    self.on_thinking(content=token, iteration=state.iteration)
                            elif _reasoning_active[0]:
                                # 推理内容正在输出 → 缓存 content token，避免正文提前泄露
                                _content_buffer.append(token)
                            else:
                                # 无推理内容 → 直接输出到正文
                                if self.on_content_token:
                                    self.on_content_token(token)

                        def _route_reasoning(token: str):
                            # 模型原生推理内容 → 标记推理活跃 + 路由到 thinking
                            _reasoning_active[0] = True
                            if self.on_thinking:
                                self.on_thinking(content=token, iteration=state.iteration)

                        def _on_stream_tools_detected():
                            _has_tools[0] = True
                            # 检测到 tool_calls 时，清空缓存的 content（不应出现在正文中）
                            _content_buffer.clear()

                        llm_response: LLMResponse = await agent.llm_client.chat_with_tools_stream(
                            messages=messages,
                            tools=tools_openai_format,
                            tool_choice="auto",
                            temperature=agent.config.get('temperature', 0.7),
                            on_content_token=_route_token,
                            on_reasoning_token=_route_reasoning,
                            on_tools_detected=_on_stream_tools_detected
                        )

                        # 流式输出结束：释放推理期间缓存的 content token
                        if not _has_tools[0]:
                            _flush_content_buffer()
                    else:
                        # 非流式模式
                        llm_response: LLMResponse = await agent.llm_client.chat_with_tools(
                            messages=messages,
                            tools=tools_openai_format,
                            tool_choice="auto",
                            temperature=agent.config.get('temperature', 0.7)
                        )

                    # 累加 token 用量
                    if llm_response.usage:
                        total_prompt_tokens += llm_response.usage.get("prompt_tokens", 0)
                        total_completion_tokens += llm_response.usage.get("completion_tokens", 0)
                        total_tokens += llm_response.usage.get("total_tokens", 0)

                    # 记录中间结果
                    state.add_intermediate_result({
                        'iteration': state.iteration,
                        'llm_response': {
                            'content': llm_response.content,
                            'tool_calls': [
                                {'name': tc.name, 'arguments': tc.arguments}
                                for tc in llm_response.tool_calls
                            ],
                            'finish_reason': llm_response.finish_reason
                        }
                    })

                    # 情况1: LLM 返回 tool_calls，执行 Skills
                    if llm_response.has_tool_calls():
                        # 硬性限制：检查是否已达到最大调用次数
                        if self.tool_call_count >= self.max_tool_calls:
                            logger.warning(f"⚠️ 已达到最大 Skill 调用次数限制 ({self.max_tool_calls})，强制生成最终答案")
                            # 强制要求 LLM 提供最终答案
                            messages.append({
                                'role': 'user',
                                'content': PromptLoader.render("agent_loop/tool_limit.j2", max_tool_calls=self.max_tool_calls)
                            })
                            continue

                        # 推理开始：计时 + 回调 thinking 内容
                        think_start = time.monotonic()
                        tool_names = [tc.name for tc in llm_response.tool_calls]
                        thinking_text = llm_response.reasoning_content or llm_response.content or f"正在分析问题，准备调用 {', '.join(tool_names)}..."
                        if self.on_thinking:
                            self.on_thinking(
                                content=thinking_text,
                                iteration=state.iteration
                            )

                        logger.info(f"LLM requested {len(llm_response.tool_calls)} tool calls (当前已调用 {self.tool_call_count}/{self.max_tool_calls})")

                        # 添加 assistant 消息（包含 tool_calls）
                        messages.append(self._create_assistant_message_with_tools(llm_response))

                        # 记录 assistant 消息到短期记忆
                        if self.short_term_memory and session_id:
                            tool_names = [tc.name for tc in llm_response.tool_calls]
                            self.short_term_memory.add_message(
                                session_id=session_id,
                                role="assistant",
                                content=f"调用工具：{', '.join(tool_names)}"
                            )
                            message_count += 1

                        # 执行每个 Skill 调用
                        for tool_call in llm_response.tool_calls:
                            # 增加计数
                            self.tool_call_count += 1
                            logger.debug(f"Executing: {tool_call.name}({tool_call.arguments}) - 第 {self.tool_call_count} 次调用")

                            # Harness Engineering: 验证调用
                            if self.validator:
                                validation_result = self.validator.validate_tool_call(
                                    agent.agent_id,
                                    tool_call.name
                                )
                                if not validation_result.get("valid"):
                                    logger.warning(
                                        f"⚠️ 约束警告: {validation_result.get('reason')}"
                                    )

                            tool_result = await agent.execute_tool(
                                tool_name=tool_call.name,
                                arguments=tool_call.arguments
                            )

                            # 回调工具步骤
                            if self.on_tool_step:
                                result_str = str(tool_result)
                                self.on_tool_step(
                                    tool_name=tool_call.name,
                                    arguments=tool_call.arguments,
                                    result=result_str[:500],
                                    iteration=state.iteration,
                                    success="error" not in result_str.lower()
                                )

                            # 添加结果消息
                            messages.append(
                                agent.llm_client.create_tool_message(
                                    tool_call_id=tool_call.id,
                                    tool_name=tool_call.name,
                                    result=tool_result
                                )
                            )

                            # 记录结果到短期记忆
                            if self.short_term_memory and session_id:
                                result_summary = str(tool_result)[:200]
                                self.short_term_memory.add_message(
                                    session_id=session_id,
                                    role="tool",
                                    content=f"{tool_call.name}: {result_summary}"
                                )
                                message_count += 1

                        # Skill 激活后：动态刷新 tools 和 system prompt
                        if tool_call.name == "activate_skill":
                            tools_openai_format = agent.get_tools_for_llm()
                            # 更新 system prompt（注入 Skill 指令正文）
                            if messages and messages[0].get("role") == "system":
                                messages[0]["content"] = agent.get_system_prompt()
                            logger.info(f"🔄 Skill activated, refreshed tools and system prompt")

                        # 推理轮次结束：回调耗时
                        elapsed = time.monotonic() - think_start
                        if self.on_thinking_done:
                            self.on_thinking_done(
                                iteration=state.iteration,
                                elapsed_seconds=round(elapsed, 1)
                            )

                        # 继续下一轮循环
                        continue

                    # 情况2: LLM 返回文本响应，任务完成
                    else:
                        logger.info(f"LLM provided final response (no tool calls)")

                        # 推送模型原生推理内容到 thinking 回调
                        if llm_response.reasoning_content and self.on_thinking:
                            self.on_thinking(
                                content=llm_response.reasoning_content,
                                iteration=state.iteration
                            )
                            if self.on_thinking_done:
                                self.on_thinking_done(
                                    iteration=state.iteration,
                                    elapsed_seconds=0
                                )

                        # Harness Engineering: 验证和修复输出
                        final_answer = llm_response.content

                        if self.validator and final_answer:
                            validation_result = self.validator.validate_output(
                                agent.agent_id,
                                final_answer
                            )

                            if not validation_result.get("valid"):
                                logger.warning(
                                    f"⚠️ 输出约束违规: {validation_result.get('violations')}"
                                )

                                # 自动修复
                                if self.auto_fixer and validation_result.get("auto_fixable"):
                                    fixed_answer = self.auto_fixer.fix_output(
                                        final_answer,
                                        validation_result.get("auto_fixable", [])
                                    )
                                    if fixed_answer != final_answer:
                                        logger.info("🔧 输出已自动修复")
                                        final_answer = fixed_answer

                        # 记录最终回答到短期记忆
                        if self.short_term_memory and session_id:
                            self.short_term_memory.add_message(
                                session_id=session_id,
                                role="assistant",
                                content=final_answer or "(empty response)"
                            )
                            message_count += 1
                            logger.debug(f"Recorded final answer to short-term memory (session={session_id})")

                        result = {
                            'answer': final_answer,
                            'iterations': state.iteration,
                            'agent_id': agent.agent_id,
                            'usage': {
                                'prompt_tokens': total_prompt_tokens,
                                'completion_tokens': total_completion_tokens,
                                'total_tokens': total_tokens,
                            },
                            'message_count': message_count,
                        }

                        # 让 Agent 进行结果后处理（如提取建议等）
                        if hasattr(agent, 'post_process_result'):
                            result = await agent.post_process_result(result, final_answer)

                        state.mark_completed(result)
                        break

                except Exception as e:
                    logger.error(f"Error in iteration {state.iteration}: {e}")
                    # 异常时也要关闭当前迭代的 thinking dots
                    if self.on_thinking_done:
                        self.on_thinking_done(
                            iteration=state.iteration,
                            elapsed_seconds=0
                        )
                    if state.iteration >= state.max_iterations:
                        state.mark_failed(str(e))
                        break
                    # 否则继续尝试

            # 如果达到最大迭代次数但没有完成
            if not state.is_completed():
                logger.warning(f"Max iterations reached without completion")

                # 强制调用 LLM 生成最终总结
                try:
                    logger.info("Forcing LLM to provide final answer")

                    # 添加强制总结的提示
                    messages.append({
                        'role': 'user',
                        'content': PromptLoader.load("agent_loop/force_answer.j2")
                    })

                    # 调用 LLM（禁用 function calling）
                    final_response = await agent.llm_client.chat_with_tools(
                        messages=messages,
                        tools=None,
                        temperature=0.7
                    )

                    result = {
                        'answer': final_response.content or '抱歉，未能完成任务',
                        'iterations': state.iteration,
                        'warning': 'max_iterations_reached',
                        'usage': {
                            'prompt_tokens': total_prompt_tokens,
                            'completion_tokens': total_completion_tokens,
                            'total_tokens': total_tokens,
                        },
                        'message_count': message_count,
                    }

                    # 记录最终回答到短期记忆
                    if self.short_term_memory and session_id:
                        self.short_term_memory.add_message(
                            session_id=session_id,
                            role="assistant",
                            content=result['answer']
                        )
                        message_count += 1

                    state.mark_completed(result)
                    logger.info("Generated fallback answer after max iterations")

                except Exception as e:
                    logger.error(f"Failed to generate fallback answer: {e}")
                    # 降级到简单提取
                    result = {
                        'answer': '抱歉，系统在处理您的问题时遇到了问题。建议您简化问题或稍后重试。',
                        'iterations': state.iteration,
                        'warning': 'max_iterations_reached',
                        'error': str(e),
                        'usage': {
                            'prompt_tokens': total_prompt_tokens,
                            'completion_tokens': total_completion_tokens,
                            'total_tokens': total_tokens,
                        },
                        'message_count': message_count,
                    }
                    state.mark_completed(result)

            logger.info(f"Agent Loop finished: status={state.status.value}, iterations={state.iteration}")
            return state.final_result or {}

        except Exception as e:
            logger.error(f"Agent Loop failed: {e}")
            state.mark_failed(str(e))
            raise

    async def _initialize_messages(self, agent, input_data: Dict[str, Any], session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """初始化消息列表，包含历史对话上下文"""
        messages = []

        # 系统提示词
        system_prompt = agent.get_system_prompt()
        if system_prompt:
            messages.append({
                'role': 'system',
                'content': system_prompt
            })

        # 加载历史对话（短期记忆）
        if self.short_term_memory and session_id:
            history = await self.short_term_memory.get_history(session_id, limit=5)  # 最近5轮对话
            if history:
                logger.info(f"Loaded {len(history)} historical messages from short-term memory")
                messages.extend(history)

        # 用户输入
        user_message = agent.format_user_input(input_data)
        messages.append({
            'role': 'user',
            'content': user_message
        })

        return messages

    def _create_assistant_message_with_tools(self, llm_response: LLMResponse) -> Dict[str, Any]:
        """创建包含 tool_calls 的 assistant 消息"""
        message = {
            'role': 'assistant',
            'content': llm_response.content or None
        }

        # 添加 tool_calls（OpenAI 格式）
        if llm_response.tool_calls:
            message['tool_calls'] = [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {
                        'name': tc.name,
                        'arguments': json.dumps(tc.arguments, ensure_ascii=False)
                    }
                }
                for tc in llm_response.tool_calls
            ]

        return message
