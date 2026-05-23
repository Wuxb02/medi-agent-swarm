"""
SwarmCoordinator：Swarm 入口和智能路由

注意：这不是编排器！
- 只负责路由决策：简单问题 → 单 Agent，复杂问题 → Swarm
- 不控制 Agent 执行
- 不编排任务顺序

类比：交通信号灯，决定车辆走哪条路，但不控制车辆如何行驶
"""
import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from loguru import logger

from core import LLMClient
from core.prompt_loader import PromptLoader
from .shared_context import SharedContext, SubTask, TaskStatus
from .lead_agent import LeadAgent
from .events import Event, EventType
from agents import ConsultationAgent, DiagnosticAgent, ResearchAgent
from memory import SessionSummaryManager, SessionSummary, ShortTermMemory, LongTermMemory, PersonalProfile


class SwarmCoordinator:
    """
    Swarm 协调器

    职责：
    1. 智能路由（简单 → 单 Agent，复杂 → Swarm）
    2. 初始化 SharedContext
    3. 启动和监控 Swarm
    4. 生成 SessionSummary

    不做：
    - 不编排 Worker 执行顺序
    - 不直接调用 Worker
    - 不控制任务分配
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        event_callback: Optional[Any] = None,
        questionnaire_manager: Optional[Any] = None
    ):
        self.llm_client = llm_client or LLMClient()
        self.event_callback = event_callback
        self.questionnaire_manager = questionnaire_manager

        # 初始化 Agent
        self.lead_agent = LeadAgent(
            llm_client=self.llm_client,
            questionnaire_manager=self.questionnaire_manager,
        )
        self.consultation_agent = ConsultationAgent()
        self.diagnostic_agent = DiagnosticAgent()
        self.research_agent = ResearchAgent()

        # Worker 池
        self.worker_pool: List[Any] = [
            self.consultation_agent,
            self.diagnostic_agent,
            self.research_agent
        ]

        # 记忆管理器
        self.session_manager = SessionSummaryManager()
        self.short_term_memory = ShortTermMemory(storage_type="memory", llm_client=self.llm_client)  # 或 "redis"
        self.long_term_memory = LongTermMemory()
        self.personal_profile = PersonalProfile()
        self.ltm_save_task = None

        # 将短期记忆和用户档案注入到所有 Worker Agent 的 Loop
        # 注意：LeadAgent 不继承 BaseAgent，没有 loop 属性，不需要注入
        personal_text = self.personal_profile.to_text()
        for worker in self.worker_pool:
            if hasattr(worker, 'loop'):
                worker.loop.short_term_memory = self.short_term_memory
                if personal_text != "暂无":
                    worker.loop.user_context = personal_text
                # 注入问卷管理器（用于交互式提问）
                if self.questionnaire_manager:
                    worker.loop.questionnaire_manager = self.questionnaire_manager

        logger.info(f"SwarmCoordinator initialized with {len(self.worker_pool)} workers")
        logger.info(f"Memory system: short_term={self.short_term_memory.storage_type}, long_term={'enabled' if self.long_term_memory.enabled else 'disabled'}")

    def _get_agent_by_id(self, agent_id: str):
        """根据 agent_id 返回对应的 Agent 实例"""
        mapping = {
            "consultation_agent": self.consultation_agent,
            "diagnostic_agent": self.diagnostic_agent,
            "research_agent": self.research_agent
        }
        return mapping.get(agent_id)

    async def process(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        处理用户问题

        Args:
            question: 用户问题
            context: 额外上下文（年龄、既往史等）
            session_id: 会话ID（如果不提供，将自动生成）

        Returns:
            处理结果
        """
        start_time = datetime.now()
        if session_id is None:
            session_id = f"{start_time.strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

        logger.info(f"Processing question (session={session_id}): {question[:50]}...")

        # 刷新 Worker Agent 的用户档案（可能在会话中被更新）
        personal_text = self.personal_profile.to_text()
        if personal_text != "暂无":
            for worker in self.worker_pool:
                if hasattr(worker, 'loop'):
                    worker.loop.user_context = personal_text

        # ===== 统一的记忆检索（所有模式都使用）=====
        # 1. 检索短期记忆（当前会话历史）
        recent_history = await self.short_term_memory.get_recent_messages(
            session_id=session_id,
            limit=10  # 最近5轮对话（10条消息）
        )

        # 2. 检索长期记忆（相似历史案例）
        similar_memories = await self.long_term_memory.search_similar_sessions(
            query=question,
            limit=3
        )

        # 3. 构建增强上下文（传给 LeadAgent 做任务分解）
        enhanced_context = context or {}

        # 注入用户档案（仅已确认信息，不含待确认条目）
        lead_personal = self.personal_profile.to_text()
        if lead_personal != "暂无":
            enhanced_context["personal_profile"] = lead_personal

        # 注入短期记忆
        if recent_history:
            enhanced_context["recent_history"] = [
                {"role": msg.get("role", ""), "content": msg.get("content", "")}
                for msg in recent_history
            ]
            logger.info(f"Loaded {len(recent_history)} recent messages from short-term memory")

        # 注入长期记忆（LeadAgent 基于此做更好的任务分解）
        if similar_memories:
            enhanced_context["historical_cases"] = [
                {
                    "summary": mem["content"],
                    "score": mem["score"]
                }
                for mem in similar_memories
            ]
            logger.info(f"Found {len(similar_memories)} similar historical cases from long-term memory")

        # Step 0: LeadAgent 信息澄清（在任务分解之前）
        clarify_result = await self.lead_agent.clarify(
            question=question,
            context=enhanced_context,
            session_id=session_id,
            event_callback=self.event_callback,
        )

        if clarify_result.get("clarified"):
            collected_info = clarify_result["collected_info"]
            enhanced_context["collected_info"] = collected_info
            logger.info(f"LeadAgent 澄清完成，收集信息: {collected_info[:100]}...")

        # Step 1: LeadAgent 分解任务
        assessment = await self.lead_agent.assess_and_decompose(question, enhanced_context)

        subtasks = assessment.get("subtasks", [])

        logger.info(f"LeadAgent 分解任务：{len(subtasks)} 个")

        # Step 2: 根据任务数量路由
        final_answer = None
        mode = None

        if len(subtasks) == 1:
            # 单任务 → 与 Swarm 模式一致，通过 process_subtask 执行（隔离历史）
            task = subtasks[0]
            agent_id = task.get("assigned_agent")
            agent = self._get_agent_by_id(agent_id)

            if agent is None:
                # 如果找不到 Agent，降级到 ConsultationAgent
                logger.warning(f"Unknown agent_id: {agent_id}, fallback to ConsultationAgent")
                agent = self.consultation_agent

            logger.info(f"Route: Single Agent ({agent_id})")
            mode = "single_agent"

            # 注入 thinking 回调（单 Agent 模式直接通过 event_callback 推送）
            if self.event_callback and hasattr(agent, 'set_on_thinking'):
                self._inject_thinking_callbacks(agent, lambda event: self.event_callback(event))

            # 构造 SubTask 对象，走与 Swarm 模式一致的 process_subtask 路径
            subtask_obj = SubTask(
                id=task.get("id", "single"),
                type=task.get("type", "general"),
                description=task.get("description", question),
                assigned_agent=agent_id,
                status=TaskStatus.PENDING,
            )
            sub_session_id = f"{session_id}:{agent_id}:{subtask_obj.id}"

            result = await agent.process_subtask(
                subtask_obj,
                session_id=session_id,
                sub_session_id=sub_session_id,
            )
            final_answer = result.get('answer', '')

            # 提取 token 用量
            token_usage = result.get('usage', {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
            session_message_count = result.get('message_count', 0)

            # 清理 thinking 回调
            if hasattr(agent, 'set_on_thinking'):
                self._cleanup_thinking_callbacks(agent)

            # 将用户问题和子会话结果合并回主会话
            await self.short_term_memory.add_message(
                session_id=session_id,
                role="user",
                content=question,
            )
            self.short_term_memory.merge_sub_session(
                main_session_id=session_id,
                sub_session_id=sub_session_id,
                summary_text=final_answer if final_answer else "",
            )

            result.update({
                'swarm_enabled': False,
                'session_id': session_id,
                'route_reason': f'单任务路由到 {agent_id}',
                'usage': token_usage,
                'agents_involved': [agent_id],
            })

            # 确保单Agent模式下也有 disclaimer 字段
            if 'disclaimer' not in result:
                result['disclaimer'] = "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"

            # 确保单Agent模式下也有 suggestions 字段
            if 'suggestions' not in result:
                result['suggestions'] = []

            # 保存单 Agent 会话总结
            try:
                end_time = datetime.now()
                summary = SessionSummary.from_single_agent(
                    session_id=session_id,
                    question=question,
                    agent_id=agent_id,
                    final_answer=final_answer,
                    start_time=start_time,
                    end_time=end_time,
                    usage=token_usage,
                    total_messages=session_message_count
                )
                self.session_manager.save_summary(summary)
            except Exception as e:
                logger.error(f"Failed to save single agent session summary: {e}")

        elif len(subtasks) >= 2:
            # 多任务 → 启动 Swarm
            logger.info(f"Route: Swarm (Multi-Agent Collaboration) - {len(subtasks)} tasks")
            mode = "swarm"
            self.ltm_save_task = None
            result = await self._process_with_swarm(
                question=question,
                context=enhanced_context,
                assessment=assessment,
                session_id=session_id,
                start_time=start_time
            )
            final_answer = result.get('answer', '')
            # ltm_save_task 已在 _process_with_swarm 中设置
            return result

        else:
            # 0个任务 → 降级到 ConsultationAgent
            logger.warning("No subtasks generated, fallback to ConsultationAgent")
            mode = "fallback"

            # 注入 thinking 回调
            if self.event_callback and hasattr(self.consultation_agent, 'set_on_thinking'):
                self._inject_thinking_callbacks(self.consultation_agent, lambda event: self.event_callback(event))

            # 降级模式也走 process_subtask 路径，隔离历史
            fallback_subtask = SubTask(
                id="fallback",
                type="general",
                description=question,
                assigned_agent="consultation_agent",
                status=TaskStatus.PENDING,
            )
            sub_session_id = f"{session_id}:consultation_agent:fallback"

            result = await self.consultation_agent.process_subtask(
                fallback_subtask,
                session_id=session_id,
                sub_session_id=sub_session_id,
            )
            final_answer = result.get('answer', '')
            token_usage = result.get('usage', {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
            session_message_count = result.get('message_count', 0)

            # 清理 thinking 回调
            if hasattr(self.consultation_agent, 'set_on_thinking'):
                self._cleanup_thinking_callbacks(self.consultation_agent)

            # 将用户问题和子会话结果合并回主会话
            await self.short_term_memory.add_message(
                session_id=session_id,
                role="user",
                content=question,
            )
            self.short_term_memory.merge_sub_session(
                main_session_id=session_id,
                sub_session_id=sub_session_id,
                summary_text=final_answer if final_answer else "",
            )
            result.update({
                'swarm_enabled': False,
                'session_id': session_id,
                'usage': token_usage,
                'agents_involved': ['consultation_agent'],
            })

            # 保存 fallback 会话总结
            try:
                end_time = datetime.now()
                summary = SessionSummary.from_single_agent(
                    session_id=session_id,
                    question=question,
                    agent_id="consultation_agent",
                    final_answer=final_answer,
                    start_time=start_time,
                    end_time=end_time,
                    usage=token_usage,
                    total_messages=session_message_count
                )
                self.session_manager.save_summary(summary)
            except Exception as e:
                logger.error(f"Failed to save fallback session summary: {e}")

        # ===== 统一的记忆保存（非 Swarm 模式）=====
        end_time = datetime.now()

        # 将 total_time 写入结果
        result['total_time'] = (end_time - start_time).total_seconds()

        # 注意：用户问题和最终回答已由上方 merge_sub_session 保存到主会话，子会话已清除

        # fire-and-forget：由 chat_stream 的 finalizer 负责等待完成
        self.ltm_save_task = asyncio.ensure_future(self._save_long_term_memory(
            session_id=session_id,
            question=question,
            answer=final_answer,
            metadata={
                "mode": mode,
                "subtasks_count": len(subtasks),
                "total_time": (end_time - start_time).total_seconds(),
                "total_tokens": token_usage.get('total_tokens', 0),
            }
        ))

        return result

    async def _process_with_swarm(
        self,
        question: str,
        context: Optional[Dict[str, Any]],
        assessment: Dict[str, Any],
        session_id: str,
        start_time: datetime
    ) -> Dict[str, Any]:
        """
        使用 Swarm 处理复杂问题

        这是群体智能的核心流程

        注意：context 已经包含了长短期记忆（在 process() 中注入）
        """
        # context 已经包含 recent_history 和 historical_cases
        # 无需重复检索

        # 创建 SharedContext
        shared_context = SharedContext(session_id=session_id)
        if self.event_callback:
            shared_context.on_event_callback = self.event_callback

        # 附加 SharedContext 到所有 Worker + 注入 thinking 回调
        for worker in self.worker_pool:
            worker.attach_shared_context(shared_context)
            self._inject_thinking_callbacks(worker, shared_context.publish_event)

        # 发布 Swarm 启动事件
        shared_context.publish_event(Event(
            type=EventType.SWARM_STARTED,
            source_agent="swarm_coordinator",
            data={
                "question": question,
                "num_subtasks": len(assessment.get("subtasks", []))
            }
        ))

        # Step 1: LeadAgent 分解任务
        subtasks = self.lead_agent.create_subtasks(assessment, shared_context)
        logger.info(f"Created {len(subtasks)} subtasks")

        # Step 2: Worker 执行分配的任务（并行）
        tasks = []
        for worker in self.worker_pool:
            task = asyncio.create_task(
                self._worker_execute_assigned_tasks(worker, shared_context)
            )
            tasks.append(task)

        # 等待所有 Worker 完成（或超时）
        timeout_occurred = False
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=90.0  # 增加超时时间到 90 秒，应对复杂案例
            )
        except asyncio.TimeoutError:
            timeout_occurred = True
            logger.warning("Swarm execution timeout (90s)")
            # 记录哪些 Agent 已完成，哪些未完成
            completed_agents = list(shared_context.agent_contributions.keys())
            claimed_tasks = [
                (subtask.assigned_to, subtask.type)
                for subtask in shared_context.task_decomposition.values()
                if subtask.status.value == "claimed"
            ]
            logger.info(f"Completed agents: {completed_agents}")
            logger.info(f"Timed out tasks: {claimed_tasks}")

        # Step 3: LeadAgent 汇总结果
        # 即使超时，也尝试汇总已完成的部分结果
        final_answer = await self.lead_agent.synthesize_results(
            question=question,
            shared_context=shared_context,
            timeout_occurred=timeout_occurred
        )

        # Step 3.5: 合并 Worker 子会话到主会话
        await self._merge_worker_subsessions(
            main_session_id=session_id,
            shared_context=shared_context,
            question=question,
        )

        end_time = datetime.now()

        # 聚合所有 Worker 的 token 用量和消息数
        swarm_prompt_tokens = 0
        swarm_completion_tokens = 0
        swarm_total_tokens = 0
        swarm_message_count = 0
        for agent_id, contributions in shared_context.agent_contributions.items():
            for contrib in contributions:
                usage = contrib.result.get('usage', {})
                swarm_prompt_tokens += usage.get('prompt_tokens', 0)
                swarm_completion_tokens += usage.get('completion_tokens', 0)
                swarm_total_tokens += usage.get('total_tokens', 0)
                swarm_message_count += contrib.result.get('message_count', 0)
        token_usage = {
            'prompt_tokens': swarm_prompt_tokens,
            'completion_tokens': swarm_completion_tokens,
            'total_tokens': swarm_total_tokens,
        }

        # Step 4: 生成 SessionSummary
        try:
            summary = SessionSummary.from_shared_context(
                session_id=session_id,
                question=question,
                shared_context=shared_context,
                final_answer=final_answer,
                start_time=start_time,
                end_time=end_time,
                usage=token_usage,
                total_messages=swarm_message_count
            )
            self.session_manager.save_summary(summary)
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}")

        # 注意：用户问题由 _merge_worker_subsessions 保存到主会话，子会话已清除

        # 保存长期记忆任务（fire-and-forget，由 chat_stream finalizer 等待）
        self.ltm_save_task = asyncio.ensure_future(self._save_long_term_memory(
            session_id=session_id,
            question=question,
            answer=final_answer,
            metadata={
                "mode": "swarm",
                "agents_count": len(shared_context.agent_contributions),
                "total_time": (end_time - start_time).total_seconds(),
                "timeout_occurred": timeout_occurred,
                "total_tokens": swarm_total_tokens,
            }
        ))

        # 发布 Swarm 完成事件
        shared_context.publish_event(Event(
            type=EventType.SWARM_COMPLETED,
            source_agent="swarm_coordinator",
            data={
                "duration": (end_time - start_time).total_seconds(),
                "agents_count": len(shared_context.agent_contributions)
            }
        ))

        # 返回结果
        completed_agents = list(shared_context.agent_contributions.keys())
        result = {
            'answer': final_answer,
            'swarm_enabled': True,
            'session_id': session_id,
            'agents_involved': completed_agents,
            'subtasks_completed': len(shared_context.get_all_completed_subtasks()),
            'total_time': (end_time - start_time).total_seconds(),
            'swarm_metadata': shared_context.get_summary(),
            'timeout_occurred': timeout_occurred,
            'usage': token_usage,
            'performance_metrics': {
                'parallel_efficiency': summary.performance.parallel_efficiency,
                'information_coverage': summary.performance.information_coverage,
                'redundancy': summary.performance.redundancy,
            }
        }

        # 提取建议和免责声明（简化实现）
        result['suggestions'] = self._extract_suggestions(final_answer)

        # 根据是否超时调整免责声明
        result['disclaimer'] = PromptLoader.render(
            "validation/swarm_disclaimer.j2",
            timeout_occurred=timeout_occurred,
            completed_agents_count=len(completed_agents),
        )

        return result

    async def _worker_execute_assigned_tasks(
        self,
        worker: Any,
        shared_context: SharedContext
    ):
        """
        Worker 执行分配给它的任务

        简化后的流程：
        - 查找分配给自己的任务
        - 执行任务
        - 记录结果
        """
        try:
            # 获取分配给该 Agent 的任务
            assigned_tasks = shared_context.get_subtasks_for_agent(worker.agent_id)

            if not assigned_tasks:
                logger.debug(f"{worker.agent_id}: No assigned tasks")
                return

            # 串行执行所有分配的任务（同一 worker 共享 agent 状态，不能并行）
            for subtask in assigned_tasks:
                logger.info(f"{worker.agent_id}: Starting {subtask.type}")
                shared_context.start_subtask(subtask.id)
                await self._execute_single_subtask(worker, subtask, shared_context)

        except Exception as e:
            logger.error(f"{worker.agent_id}: Error processing subtask: {e}")

    async def _execute_single_subtask(self, worker, subtask, shared_context):
        """执行单个子任务（使用子会话隔离）"""
        try:
            # 生成子会话 ID：{main_session_id}:{agent_id}:{subtask_id}
            # 保证同一 worker 的多个子任务也不互相污染
            sub_session_id = f"{shared_context.session_id}:{worker.agent_id}:{subtask.id}"

            logger.info(
                f"{worker.agent_id}: Executing {subtask.type} "
                f"(sub_session={sub_session_id})"
            )

            result = await worker.process_subtask(
                subtask,
                session_id=shared_context.session_id,
                sub_session_id=sub_session_id
            )
            shared_context.complete_subtask(subtask.id, worker.agent_id, result)
            logger.info(f"{worker.agent_id}: Completed {subtask.type}")
        except Exception as e:
            logger.error(f"{worker.agent_id}: Error in {subtask.type}: {e}")

    async def _merge_worker_subsessions(
        self,
        main_session_id: str,
        shared_context,
        question: str,
    ):
        """
        将所有 Worker 的子会话历史合并到主会话

        策略：每个 Worker 的最终回答作为一条 assistant 消息写入主会话，
        格式为 [{agent_name} - {task_type}] {answer}
        然后清除所有子会话
        """
        agent_name_map = {
            "consultation_agent": "问诊Agent",
            "diagnostic_agent": "诊断Agent",
            "research_agent": "研究Agent",
        }

        # 保存用户问题到主会话（确保下轮有完整 Q&A 上下文）
        await self.short_term_memory.add_message(
            session_id=main_session_id,
            role="user",
            content=question,
        )

        for agent_id, contributions in shared_context.agent_contributions.items():
            for contrib in contributions:
                subtask = shared_context.get_subtask(contrib.subtask_id)
                sub_session_id = f"{main_session_id}:{agent_id}:{contrib.subtask_id}"

                # 从结果中提取最终回答
                answer = contrib.result.get("answer", "")
                if not answer:
                    answer = f"[{agent_id}] 未能完成 {contrib.subtask_id}"

                agent_name = agent_name_map.get(agent_id, agent_id)
                task_type = subtask.type if subtask else "analysis"

                # 构建合并消息，保留完整内容
                summary = f"[{agent_name} - {task_type}] {answer}"

                self.short_term_memory.merge_sub_session(
                    main_session_id=main_session_id,
                    sub_session_id=sub_session_id,
                    summary_text=summary,
                    role="assistant"
                )

                logger.info(
                    f"Merged sub-session {sub_session_id} "
                    f"({len(answer)} chars) into main session"
                )

        # 清理残留的子会话（超时或异常的 Worker）
        orphaned = self.short_term_memory.get_sub_sessions(main_session_id)
        for sub_session in orphaned:
            logger.warning(f"Cleaning up orphaned sub-session: {sub_session.session_id}")
            self.short_term_memory.clear_session(sub_session.session_id)

    async def _evaluate_and_extract_memory(
        self, session_id: str, question: str, answer: str
    ) -> Optional[Dict[str, Any]]:
        """使用 LLM 评估对话质量并提取信息。

        Returns:
            {"stable_info": [...], "medical_records": [...],
             "reusable_facts": [...], "score": N, "reason": "..."}
            或 None（降级）
        """
        # 1. 获取已有信息
        existing_personal_text = self.personal_profile.to_text()

        session = self.short_term_memory.get_session(session_id)
        existing_facts = []
        if session:
            existing_facts = session.metadata.get("extracted_facts", [])

        if existing_facts:
            existing_facts_text = "\n".join(
                f"- [{f.get('category', '')}] {f.get('fact', '')}"
                for f in existing_facts
            )
        else:
            existing_facts_text = "暂无"

        # 2. 渲染 prompt
        prompt = PromptLoader.render(
            "memory/quality_eval.j2",
            existing_personal=existing_personal_text,
            existing_facts=existing_facts_text,
            current_question=question,
            current_answer=answer[:2000],
        )

        # 3. 异步调用 LLM（10 秒超时）
        try:
            response = await asyncio.wait_for(
                self.llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=1024,
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Memory eval LLM timeout (60s), session={session_id}")
            return None
        except Exception as e:
            logger.warning(f"Memory eval LLM failed: {e}, session={session_id}")
            return None

        # 4. 解析 JSON
        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                text = text.rsplit("```", 1)[0]
            result = json.loads(text.strip())
        except (json.JSONDecodeError, IndexError) as e:
            logger.warning(f"Memory eval JSON parse failed: {e}, session={session_id}")
            return None

        # 5. 兼容旧格式（只有 facts / personal_info 字段）
        if "reusable_facts" not in result and "facts" in result:
            result["reusable_facts"] = result.pop("facts")
        # 旧字段名 personal_info → stable_info
        if "stable_info" not in result and "personal_info" in result:
            result["stable_info"] = result.pop("personal_info")
        if "stable_info" not in result:
            result["stable_info"] = []
        if "medical_records" not in result:
            result["medical_records"] = []

        # 6. 将新可复用事实追加到 session metadata
        new_facts = result.get("reusable_facts", [])
        if session and new_facts:
            existing_facts.extend(new_facts)
            session.metadata["extracted_facts"] = existing_facts

        return result

    async def _save_long_term_memory(self, session_id, question, answer, metadata):
        """异步保存长期记忆（LLM 评估 + 信息分类存储）"""
        try:
            eval_result = await self._evaluate_and_extract_memory(
                session_id, question, answer
            )

            if eval_result is not None:
                score = eval_result.get("score", 0)
                stable_info = eval_result.get("stable_info", [])
                medical_records = eval_result.get("medical_records", [])

                # 稳定信息 → 暂存区（score >= 3 才写入，过滤寒暄）
                if stable_info and score >= 3:
                    self.personal_profile.add_pending(stable_info)
                    for item in stable_info:
                        logger.info(f"  [Pending-Info] {item['key']}：{item['value']}")

                # 病史记录 → 暂存区（所有提取到的病史都进暂存区）
                if medical_records:
                    self.personal_profile.add_pending_records(medical_records)
                    for rec in medical_records:
                        logger.info(f"  [Pending-Record] [{rec.get('date', '')}] {rec.get('description', '')}")

                # Mem0 门控
                if score < 5:
                    logger.info(
                        f"Memory gate: SKIP Mem0 (score={score}) "
                        f"reason={eval_result.get('reason', '')} session={session_id}"
                    )
                    # 打印本轮存储摘要
                    session = self.short_term_memory.get_session(session_id)
                    msg_count = len(session.messages) if session else 0
                    logger.info(
                        f"Memory turn summary — short_term={msg_count} msgs | "
                        f"pending_info={len(stable_info)} pending_records={len(medical_records)} | mem0=SKIP"
                    )
                    return

                # 可复用事实 → Mem0
                reusable_facts = eval_result.get("reusable_facts", [])
                if reusable_facts:
                    memory_text = "; ".join(f["fact"] for f in reusable_facts)
                    for f in reusable_facts:
                        logger.info(f"  [Mem0] [{f.get('category', '')}] {f['fact']}")
                else:
                    memory_text = f"Q: {question[:200]} A: {answer[:300]}"
                logger.info(
                    f"Memory gate: PASS score={score} "
                    f"facts={len(reusable_facts)} pending_info={len(stable_info)} pending_records={len(medical_records)} session={session_id}"
                )
            else:
                memory_text = f"Q: {question[:200]} A: {answer[:300]}"
                logger.info(f"Memory gate: FALLBACK (LLM eval unavailable) session={session_id}")
                logger.info(f"  [Mem0] (raw) {memory_text[:100]}...")

            await self.long_term_memory.add_session_summary(
                session_id=session_id,
                question=memory_text,
                answer="",
                metadata={
                    **metadata,
                    "quality_score": eval_result.get("score") if eval_result else None,
                },
            )

            # 打印本轮存储摘要
            session = self.short_term_memory.get_session(session_id)
            msg_count = len(session.messages) if session else 0
            if eval_result:
                info_count = len(eval_result.get("stable_info", []))
                records_count = len(eval_result.get("medical_records", []))
            else:
                info_count = 0
                records_count = 0
            logger.info(
                f"Memory turn summary — short_term={msg_count} msgs | "
                f"pending_info={info_count} pending_records={records_count} | "
                f"mem0={'PASS' if eval_result else 'FALLBACK'}"
            )
        except Exception as e:
            logger.error(f"LTM save failed (session={session_id}): {e}")

    def _inject_thinking_callbacks(self, worker, publish_fn):
        """为 Worker Agent 注入 thinking/tool_step/thinking_done 回调"""
        agent_id = worker.agent_id

        def _on_thinking(content, iteration):
            publish_fn(Event(
                type=EventType.AGENT_THINKING,
                source_agent=agent_id,
                data={"content": content, "iteration": iteration}
            ))

        def _on_tool_step(tool_name, arguments, result, iteration, success):
            publish_fn(Event(
                type=EventType.AGENT_TOOL_STEP,
                source_agent=agent_id,
                data={
                    "tool_name": tool_name,
                    "arguments": {k: str(v)[:100] for k, v in arguments.items()} if isinstance(arguments, dict) else str(arguments)[:100],
                    "result": result,
                    "iteration": iteration,
                    "success": success
                }
            ))

        def _on_thinking_done(iteration, elapsed_seconds):
            publish_fn(Event(
                type=EventType.AGENT_THINKING_DONE,
                source_agent=agent_id,
                data={"iteration": iteration, "elapsed_seconds": elapsed_seconds}
            ))

        worker.set_on_thinking(_on_thinking)
        worker.set_on_tool_step(_on_tool_step)
        worker.set_on_thinking_done(_on_thinking_done)

        def _on_content_token(token):
            publish_fn(Event(
                type=EventType.AGENT_CONTENT_DELTA,
                source_agent=agent_id,
                data={"token": token}
            ))

        worker.set_on_content_token(_on_content_token)

        def _on_questionnaire(questionnaire_id, questionnaire_data):
            publish_fn(Event(
                type=EventType.AGENT_QUESTIONNAIRE,
                source_agent=agent_id,
                data={
                    "questionnaire_id": questionnaire_id,
                    "questionnaire_data": questionnaire_data,
                }
            ))

        worker.set_on_questionnaire(_on_questionnaire)

    def _cleanup_thinking_callbacks(self, worker):
        """清理 Worker Agent 的 thinking 回调"""
        worker.set_on_thinking(None)
        worker.set_on_tool_step(None)
        worker.set_on_thinking_done(None)
        worker.set_on_content_token(None)
        worker.set_on_questionnaire(None)

    def _extract_suggestions(self, final_answer: str) -> List[str]:
        """从最终答案中提取建议（简化实现）"""
        suggestions = []

        # 简单的文本匹配
        if "【核心建议】" in final_answer:
            # 提取核心建议部分
            start_idx = final_answer.find("【核心建议】")
            end_idx = final_answer.find("【", start_idx + 1)
            if end_idx == -1:
                end_idx = len(final_answer)

            suggestions_text = final_answer[start_idx:end_idx]

            # 提取编号列表
            import re
            matches = re.findall(r'\d+\.\s*([^\n]+)', suggestions_text)
            suggestions = matches[:5]  # 最多5条

        return suggestions or ["请遵循医嘱，注意休息和营养"]

async def process_with_swarm(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    便捷函数：使用 Swarm 处理问题

    Args:
        question: 用户问题
        context: 额外上下文
        session_id: 会话ID（如果提供，将使用该ID而不是生成新的）

    Returns:
        处理结果
    """
    coordinator = SwarmCoordinator()
    return await coordinator.process(question, context, session_id=session_id)
