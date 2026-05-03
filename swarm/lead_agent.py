"""
LeadAgent：任务分解和结果汇总

注意：LeadAgent 不是编排器！
- 只负责分解任务和汇总结果
- 不控制 Worker 的执行顺序
- 不直接调用 Worker
- Worker 自主认领任务并执行
"""
import uuid
from typing import Dict, Any, List, Optional
from loguru import logger

from core.llm_client import LLMClient
from core.prompt_loader import PromptLoader
from .shared_context import SharedContext, SubTask, TaskStatus
from .events import Event, EventType


class LeadAgent:
    """
    Lead Agent：任务协调者

    职责：
    1. 评估问题复杂度
    2. 分解复杂任务为独立子任务
    3. 等待 Worker 完成
    4. 汇总所有结果

    不做：
    - 不编排执行顺序
    - 不分配任务给特定 Agent
    - 不控制 Worker 行为
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.agent_id = "lead_agent"
        self.llm_client = llm_client or LLMClient()

    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return PromptLoader.load("swarm/lead_system.j2")

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
                context=context or '无'
            )}
        ]

        try:
            content = await self.llm_client.chat(messages)

            logger.debug(f"LeadAgent assessment: {content[:200]}...")

            import json
            import re

            # 尝试提取 JSON
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result

            # 回退：假设是简单问题，交给 ConsultationAgent
            return {
                "subtasks": [{
                    "type": "knowledge_search",
                    "description": "回答用户问题",
                    "assigned_agent": "consultation_agent"
                }],
                "reason": "无法解析 LLM 响应，默认使用 ConsultationAgent"
            }

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

    async def wait_for_completion(
        self,
        shared_context: SharedContext,
        timeout: float = 30.0
    ) -> bool:
        """
        等待所有子任务完成

        这不是"主动控制"，而是"被动等待"
        Worker 自主完成任务，Lead 只是等待
        """
        import asyncio

        start_time = asyncio.get_event_loop().time()

        while True:
            if shared_context.is_all_subtasks_completed():
                logger.info("All subtasks completed")
                return True

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning(f"Timeout waiting for subtasks ({timeout}s)")
                return False

            await asyncio.sleep(0.5)  # 每 0.5 秒检查一次

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
