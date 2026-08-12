"""
LeadAgent：任务分解和结果汇总

注意：LeadAgent 不是编排器！
- 只负责分解任务和汇总结果
- 不控制 Worker 的执行顺序
- 不直接调用 Worker
- Worker 自主认领任务并执行
"""
import uuid
import time
from typing import Dict, Any, List, Optional, Callable
from loguru import logger

from mediZJ.core.llm_client import LLMClient
from mediZJ.core.prompt_loader import PromptLoader
from .shared_context import SharedContext, SubTask
from .events import Event, EventType


# question_for_user 的 OpenAI function calling schema
_QUESTION_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "question_for_user",
        "description": (
            "向用户发送结构化问卷，收集诊断所需信息。"
            "支持单选(enum)、多选(multi)、文本输入(input)三种题型。"
            "在诊断前收集患者背景信息时使用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "questionnaire": {
                    "type": "string",
                    "description": (
                        "XML 格式的问卷，包含 <questions> 标签包裹的 <question> 元素。"
                        "每个问题有 header（标题）、type（enum/multi/input）、"
                        "text（问题文本）和 suggest（选项）。"
                        "例：<questions><question header='年龄' type='input'>"
                        "<text>您的年龄是？</text></question></questions>"
                    ),
                }
            },
            "required": ["questionnaire"],
        },
    },
}


class LeadAgent:
    """
    Lead Agent：任务协调者

    职责：
    0. **信息澄清（clarify）**：通过结构化问卷收集用户背景信息
    1. 评估问题复杂度
    2. 分解复杂任务为独立子任务
    3. 等待 Worker 完成
    4. 汇总所有结果

    不做：
    - 不编排执行顺序
    - 不分配任务给特定 Agent
    - 不控制 Worker 行为
    """

    def __init__(self, llm_client: Optional[LLMClient] = None,
                 questionnaire_manager: Optional[Any] = None):
        self.agent_id = "lead_agent"
        self.llm_client = llm_client or LLMClient()
        self.questionnaire_manager = questionnaire_manager
        self.on_thinking: Optional[Callable] = None
        self.on_thinking_done: Optional[Callable] = None

    def set_on_thinking(self, callback: Optional[Callable]):
        self.on_thinking = callback

    def set_on_thinking_done(self, callback: Optional[Callable]):
        self.on_thinking_done = callback

    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return PromptLoader.load("swarm/lead_system.j2")

    def _get_clarify_system_prompt(self) -> str:
        """获取澄清阶段的系统提示词"""
        return PromptLoader.load("swarm/lead_clarify.j2")

    async def chat_reply(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        event_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """闲聊模式回复：以聊天机器人角色直接回应非医疗输入（others 意图）。

        不做任务分解、不调用 Worker Agent。简单寒暄/致谢/无关话题在此直接回应，
        但若用户输入涉及医疗诉求（医学安全优先），则引导回医疗主题。

        Args:
            question: 用户原始问题
            context: 已有上下文（记忆等）
            event_callback: 事件回调（用于流式推送）

        Returns:
            {"answer": 回复文本}
        """
        # 发射 thinking 开始
        iteration = 1
        think_start = time.monotonic()
        if self.on_thinking:
            self.on_thinking(
                content=f"正在以聊天模式回应：「{question[:200]}」",
                iteration=iteration,
            )

        # 注入近期对话历史（OpenAI 格式 user/assistant 消息，供"我刚才问了什么"等召回）
        history_messages = []
        if context and isinstance(context.get("recent_history"), list):
            for msg in context["recent_history"]:
                role = msg.get("role")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    history_messages.append({"role": role, "content": content})

        context_text = "无"
        if context:
            parts = []
            if context.get("personal_profile") and context["personal_profile"] != "暂无":
                parts.append(f"## 用户档案\n{context['personal_profile']}")
            if parts:
                context_text = "\n\n".join(parts)

        try:
            messages = [
                {"role": "system", "content": PromptLoader.load("swarm/chat_reply.j2")},
                *history_messages,
                {"role": "user", "content": PromptLoader.render(
                    "swarm/chat_reply_user.j2",
                    question=question,
                    context=context_text,
                )},
            ]

            if event_callback:
                def _on_content_token(token: str) -> None:
                    event_callback(Event(
                        type=EventType.AGENT_CONTENT_DELTA,
                        source_agent=self.agent_id,
                        data={"token": token, "is_final": True},
                    ))

                answer = await self.llm_client.chat_with_tools_stream(
                    messages=messages,
                    tools=None,
                    temperature=0.7,
                    on_content_token=_on_content_token,
                )
                # chat_with_tools_stream 返回 LLMResponse，取 content
                answer_text = answer.content or ""
            else:
                answer_text = await self.llm_client.chat(
                    messages,
                    temperature=0.7,
                )
        except Exception as e:
            logger.error(f"LeadAgent chat_reply error: {e}")
            answer_text = "抱歉，我暂时无法回应。请问有什么健康问题需要帮助吗？"

        # 发射 thinking_done
        if self.on_thinking_done:
            elapsed = round(time.monotonic() - think_start, 1)
            self.on_thinking_done(iteration=iteration, elapsed_seconds=elapsed)

        return {"answer": answer_text}

    async def assess_and_decompose(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        分析问题并分解为子任务

        返回：
        - subtasks: List[SubTask] - 子任务列表
          每个子任务包含：type（工具名）、description（描述）、assigned_agent（负责的Agent）
        """
        # 发射 thinking 开始
        iteration = 1
        think_start = time.monotonic()
        if self.on_thinking:
            self.on_thinking(
                content=f"正在分析用户问题：「{question[:500]}{'...' if len(question) > 300 else ''}」",
                iteration=iteration,
            )

        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": PromptLoader.render(
                "swarm/assessment_user.j2",
                question=question,
                personal_profile=(context or {}).get("personal_profile"),
                collected_info=(context or {}).get("collected_info"),
                recent_history=(context or {}).get("recent_history"),
                historical_cases=(context or {}).get("historical_cases"),
            )}
        ]

        try:
            if self.on_thinking:
                response = await self.llm_client.chat_with_tools_stream(
                    messages=messages,
                    tools=None,
                    response_format={"type": "json_object"},
                    on_reasoning_token=lambda token: self.on_thinking(
                        content=token,
                        iteration=iteration,
                    ),
                )
                content = response.content or ""
            else:
                content = await self.llm_client.chat(
                    messages,
                    response_format={"type": "json_object"},
                )

            logger.debug(f"LeadAgent assessment: {content[:200]}...")

            import json

            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"LeadAgent JSON 解析失败: {e}, content={content[:200]}")
                result = {
                    "subtasks": [{
                        "type": "knowledge_search",
                        "description": "回答用户问题",
                        "assigned_agent": "consultation_agent"
                    }],
                    "reason": "无法解析 LLM 响应，默认使用 ConsultationAgent",
                    "_parse_error": str(e),
                }

            # JSON Schema 校验
            if not isinstance(result, dict) or "subtasks" not in result:
                logger.warning(f"LeadAgent 输出不符合 Schema，回退: {str(result)[:200]}")
                result = {
                    "subtasks": [{
                        "type": "knowledge_search",
                        "description": "回答用户问题",
                        "assigned_agent": "consultation_agent"
                    }],
                    "reason": "LLM 输出不符合预期格式，默认使用 ConsultationAgent",
                    "_schema_violation": True,
                }

            # 校验每个 subtask 的必需字段
            for i, st in enumerate(result.get("subtasks", [])):
                if not isinstance(st, dict):
                    logger.warning(f"LeadAgent subtask[{i}] 非 dict，回退")
                    result = {
                        "subtasks": [{
                            "type": "knowledge_search",
                            "description": "回答用户问题",
                            "assigned_agent": "consultation_agent"
                        }],
                        "reason": "subtask 格式异常，默认使用 ConsultationAgent",
                        "_schema_violation": True,
                    }
                    break
                # 确保必需字段存在
                st.setdefault("type", "knowledge_search")
                st.setdefault("description", "回答用户问题")
                st.setdefault("assigned_agent", "consultation_agent")

            # 发射 thinking 内容：以可读文本描述分解结果
            if self.on_thinking:
                subtasks_list = result.get("subtasks", [])
                agent_name_map = {
                    "consultation_agent": "健康咨询",
                    "diagnostic_agent": "症状诊断",
                    "research_agent": "医学研究",
                }
                thinking_parts = [f"问题分解完成，共 {len(subtasks_list)} 个子任务："]
                for i, st in enumerate(subtasks_list, 1):
                    agent_display = agent_name_map.get(st.get("assigned_agent", ""), st.get("assigned_agent", "未知"))
                    thinking_parts.append(
                        f"{i}. {agent_display} Agent — {st.get('description', '未知任务')}"
                    )
                if result.get("reason"):
                    thinking_parts.append(f"\n分解依据：{result['reason']}")
                self.on_thinking(content="\n".join(thinking_parts), iteration=iteration)

            # 发射 thinking_done
            if self.on_thinking_done:
                elapsed = round(time.monotonic() - think_start, 1)
                self.on_thinking_done(iteration=iteration, elapsed_seconds=elapsed)

            return result

        except Exception as e:
            logger.error(f"LeadAgent assessment error: {e}")
            if self.on_thinking_done:
                elapsed = round(time.monotonic() - think_start, 1)
                self.on_thinking_done(iteration=iteration, elapsed_seconds=elapsed)
            return {
                "subtasks": [],
                "reason": f"评估失败：{e}"
            }

    def create_subtasks(
        self,
        decomposition_result: Dict[str, Any],
        shared_context: SharedContext
    ) -> List[SubTask]:
        """
        根据分解结果创建 SubTask 并发布到 SharedContext

        直接指定 assigned_agent（中心化分配）
        """
        subtasks_data = decomposition_result.get("subtasks", [])
        subtasks = []

        for data in subtasks_data:
            # 自动推断 type（基于 assigned_agent，向后兼容）
            # LeadAgent 不再输出 type 字段，这里根据 Agent 生成通用 type
            assigned_agent = data["assigned_agent"]
            inferred_type = data.get("type") or f"{assigned_agent}_task"

            subtask = SubTask(
                id=str(uuid.uuid4()),
                type=inferred_type,
                description=data["description"],
                assigned_agent=assigned_agent
            )

            shared_context.add_subtask(subtask)
            subtasks.append(subtask)

            logger.info(
                f"Created SubTask: {subtask.type} "
                f"(assigned to: {subtask.assigned_agent})"
            )

        return subtasks

    async def synthesize_results(
        self,
        question: str,
        shared_context: SharedContext,
        timeout_occurred: bool = False,
        event_callback: Optional[Callable] = None,
    ) -> str:
        """
        汇总所有 Agent 的贡献，生成最终答案

        这是 Lead Agent 的核心价值：整合多个视角

        Args:
            question: 用户问题
            shared_context: 共享上下文
            timeout_occurred: 是否发生超时
        """
        # 发射 thinking 开始
        iteration = 2  # iteration 1 = assess_decompose, iteration 2 = synthesize
        think_start = time.monotonic()
        if self.on_thinking:
            completed_count = len(shared_context.agent_contributions)
            self.on_thinking(
                content=f"正在综合 {completed_count} 个 Agent 的分析结果{'(部分超时)' if timeout_occurred else ''}...",
                iteration=iteration,
            )

        # 收集所有贡献
        all_contributions = shared_context.get_contributions()

        if not all_contributions:
            # 如果没有任何贡献
            if timeout_occurred:
                result = PromptLoader.load("swarm/timeout_fallback.j2")
            else:
                result = "抱歉，Swarm 未能提供有效分析结果。"
            if self.on_thinking_done:
                elapsed = round(time.monotonic() - think_start, 1)
                self.on_thinking_done(iteration=iteration, elapsed_seconds=elapsed)
            return result

        # 构建汇总提示
        contributions_text = []
        completed_agents = []
        for contrib in all_contributions:
            # contrib.result 是 dict，提取 answer 文本
            answer_text = contrib.result.get("answer", "") if isinstance(contrib.result, dict) else str(contrib.result)
            if answer_text:
                contributions_text.append(answer_text)
            completed_agents.append(contrib.agent_id)

        # 如果发生超时，添加说明
        timeout_note = ""
        if timeout_occurred:
            all_subtasks = shared_context.task_decomposition.values()
            incomplete_tasks = [
                subtask.type for subtask in all_subtasks
                if subtask.status.value in ["pending", "claimed"]
            ]
            if incomplete_tasks:
                timeout_note = f"""

**注意**：由于系统响应超时，以下分析模块未能完成：{', '.join(incomplete_tasks)}
以下是基于已完成的 {len(completed_agents)} 个 Agent 的部分分析结果。"""

        synthesis_prompt = PromptLoader.render(
            "swarm/synthesis.j2",
            question=question,
            contributions_text=contributions_text,
            timeout_note=timeout_note,
            timeout_occurred=timeout_occurred,
        )

        try:
            messages = [{"role": "user", "content": synthesis_prompt}]
            if event_callback:
                def _on_content_token(token: str) -> None:
                    event_callback(Event(
                        type=EventType.AGENT_CONTENT_DELTA,
                        source_agent=self.agent_id,
                        data={"token": token, "is_final": True},
                    ))

                response = await self.llm_client.chat_with_tools_stream(
                    messages=messages,
                    tools=None,
                    on_content_token=_on_content_token,
                    on_reasoning_token=(
                        lambda token: self.on_thinking(
                            content=token,
                            iteration=iteration,
                        )
                        if self.on_thinking
                        else None
                    ),
                )
                response_text = response.content or ""
            else:
                response_text = await self.llm_client.chat(messages)

            # 发射 thinking_done
            if self.on_thinking_done:
                elapsed = round(time.monotonic() - think_start, 1)
                self.on_thinking_done(iteration=iteration, elapsed_seconds=elapsed)

            return response_text

        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            if self.on_thinking_done:
                elapsed = round(time.monotonic() - think_start, 1)
                self.on_thinking_done(iteration=iteration, elapsed_seconds=elapsed)
            return f"汇总结果时出错：{e}"
