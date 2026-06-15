"""
LeadAgent：任务分解和结果汇总

注意：LeadAgent 不是编排器！
- 只负责分解任务和汇总结果
- 不控制 Worker 的执行顺序
- 不直接调用 Worker
- Worker 自主认领任务并执行
"""
import asyncio
import uuid
import json
import re
from typing import Dict, Any, List, Optional
from loguru import logger

from core.llm_client import LLMClient
from core.prompt_loader import PromptLoader
from core.tools.questionnaire import (
    create_question_for_user_tool,
    parse_questionnaire,
    format_answers_for_llm,
    _build_questionnaire_data,
)
from .shared_context import SharedContext, SubTask, TaskStatus
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

    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return PromptLoader.load("swarm/lead_system.j2")

    def _get_clarify_system_prompt(self) -> str:
        """获取澄清阶段的系统提示词"""
        return PromptLoader.load("swarm/lead_clarify.j2")

    async def clarify(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        event_callback: Optional[Any] = None,
        clarify_timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """
        信息澄清阶段：通过结构化问卷收集用户背景信息

        在任务分解之前执行。如果 LLM 判断需要更多信息，
        会调用 question_for_user 工具发送问卷给用户。

        Args:
            question: 用户原始问题
            context: 已有上下文（记忆等）
            session_id: 会话 ID
            event_callback: 事件回调（用于推送问卷到前端）
            clarify_timeout: 问卷等待超时秒数（默认 30s）

        Returns:
            {
                "clarified": bool,        # 是否进行了澄清
                "collected_info": str,     # 收集到的信息文本（用于后续注入）
                "raw_answers": dict,       # 原始答案字典
                "timeout_skipped": bool,   # 是否因超时跳过（区分于 LLM 判断无需澄清）
            }
        """
        if not self.questionnaire_manager:
            logger.debug("QuestionnaireManager 未配置，跳过澄清阶段")
            return {"clarified": False, "collected_info": "", "raw_answers": {}}

        # 构建上下文文本
        context_text = "无"
        if context:
            parts = []
            if context.get("personal_profile") and context["personal_profile"] != "暂无":
                parts.append(f"## 用户档案\n{context['personal_profile']}")
            if context.get("recent_history") and isinstance(context["recent_history"], list):
                parts.append(f"近期对话: {len(context['recent_history'])} 条消息")
            if context.get("historical_cases") and isinstance(context["historical_cases"], list):
                parts.append(f"历史相似案例: {len(context['historical_cases'])} 个")
            if parts:
                context_text = "\n\n".join(parts)

        messages = [
            {"role": "system", "content": self._get_clarify_system_prompt()},
            {"role": "user", "content": PromptLoader.render(
                "swarm/lead_clarify_user.j2",
                question=question,
                context=context_text,
            )},
        ]

        collected_answers: Dict[str, Any] = {}

        # 最多进行 2 轮澄清（避免无限循环）
        for round_num in range(2):
            try:
                response = await self.llm_client.chat_with_tools(
                    messages=messages,
                    tools=[_QUESTION_TOOL_SCHEMA],
                    tool_choice="auto",
                    temperature=0.3,
                )
            except Exception as e:
                logger.error(f"LeadAgent clarify LLM error: {e}")
                break

            if not response.has_tool_calls():
                # LLM 不需要更多信息，直接返回
                logger.info(f"LeadAgent clarify: 无需额外信息（round {round_num}）")
                break

            # 处理工具调用
            tool_call = response.tool_calls[0]
            if tool_call.name != "question_for_user":
                logger.warning(f"LeadAgent clarify: 未预期的工具调用 {tool_call.name}")
                break

            questionnaire_xml = tool_call.arguments.get("questionnaire", "")

            try:
                questions = parse_questionnaire(questionnaire_xml)
            except Exception as e:
                logger.error(f"LeadAgent clarify: 问卷解析失败: {e}")
                break

            questionnaire_id = str(uuid.uuid4())
            questionnaire_data = _build_questionnaire_data(questions)

            logger.info(f"LeadAgent clarify: 发送问卷 {questionnaire_id}，共 {len(questions)} 个问题")

            # 通过事件回调推送问卷到前端
            if event_callback:
                event_callback(Event(
                    type=EventType.AGENT_QUESTIONNAIRE,
                    source_agent=self.agent_id,
                    data={
                        "questionnaire_id": questionnaire_id,
                        "questionnaire_data": questionnaire_data,
                    },
                ))

            # 等待用户回答
            try:
                answers = await self.questionnaire_manager.create_pending(
                    questionnaire_id, timeout=clarify_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"LeadAgent clarify: 问卷超时（{clarify_timeout}s），跳过澄清")
                return {
                    "clarified": False,
                    "collected_info": "",
                    "raw_answers": {},
                    "timeout_skipped": True,
                }
            except Exception:
                logger.warning(f"LeadAgent clarify: 问卷异常，跳过澄清")
                break

            formatted = format_answers_for_llm(questions, answers)
            collected_answers.update(answers)

            # 将工具调用和结果加入消息历史（让 LLM 知道已收集了什么）
            messages.append({
                "role": "assistant",
                "content": response.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ],
            })
            messages.append(self.llm_client.create_tool_message(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                result={"success": True, "formatted_text": formatted},
            ))

        if not collected_answers:
            return {"clarified": False, "collected_info": "", "raw_answers": {}}

        # 汇总所有收集到的信息
        all_info_parts = []
        for key, val in collected_answers.items():
            if isinstance(val, list):
                all_info_parts.append(f"{key}: {', '.join(str(v) for v in val)}")
            else:
                all_info_parts.append(f"{key}: {val}")
        collected_info = "\n".join(all_info_parts)

        logger.info(f"LeadAgent clarify 完成，收集信息: {collected_info[:200]}")

        return {
            "clarified": True,
            "collected_info": collected_info,
            "raw_answers": collected_answers,
        }

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
            content = await self.llm_client.chat(
                messages,
                response_format={'type': 'json_object'}
            )

            logger.debug(f"LeadAgent assessment: {content[:200]}...")

            import json

            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"LeadAgent JSON 解析失败: {e}, content={content[:200]}")
                return {
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
                return {
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
                    return {
                        "subtasks": [{
                            "type": "knowledge_search",
                            "description": "回答用户问题",
                            "assigned_agent": "consultation_agent"
                        }],
                        "reason": "subtask 格式异常，默认使用 ConsultationAgent",
                        "_schema_violation": True,
                    }
                # 确保必需字段存在
                st.setdefault("type", "knowledge_search")
                st.setdefault("description", "回答用户问题")
                st.setdefault("assigned_agent", "consultation_agent")

            return result

        except Exception as e:
            logger.error(f"LeadAgent assessment error: {e}")
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
        timeout_occurred: bool = False
    ) -> str:
        """
        汇总所有 Agent 的贡献，生成最终答案

        这是 Lead Agent 的核心价值：整合多个视角

        Args:
            question: 用户问题
            shared_context: 共享上下文
            timeout_occurred: 是否发生超时
        """
        # 收集所有贡献
        all_contributions = shared_context.get_contributions()

        if not all_contributions:
            # 如果没有任何贡献
            if timeout_occurred:
                return PromptLoader.load("swarm/timeout_fallback.j2")
            else:
                return "抱歉，Swarm 未能提供有效分析结果。"

        # 构建汇总提示
        contributions_text = []
        completed_agents = []
        for contrib in all_contributions:
            subtask = shared_context.get_subtask(contrib.subtask_id)
            contributions_text.append(
                f"**{contrib.agent_id}** ({subtask.type if subtask else '未知'}):\n"
                f"{contrib.result}"
            )
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
            response = await self.llm_client.chat([
                {"role": "user", "content": synthesis_prompt}
            ])

            return response

        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            return f"汇总结果时出错：{e}"
