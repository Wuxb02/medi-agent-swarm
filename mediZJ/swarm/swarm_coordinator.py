"""
SwarmCoordinator：Swarm 入口和智能路由

注意：这不是编排器！
- 只负责路由决策：简单问题 → 单 Agent，复杂问题 → Swarm
- 不控制 Agent 执行
- 不编排任务顺序

类比：交通信号灯，决定车辆走哪条路，但不控制车辆如何行驶

双轨运行：通过 MEDIZJ_USE_LANGGRAPH 环境变量控制
- false（默认）：走原有 AgentLoop + asyncio.wait 路径
- true：走 LangGraph SupervisorGraph + Send API 路径
"""
import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from loguru import logger

from mediZJ.core import LLMClient
from mediZJ.core.prompt_loader import PromptLoader
from .shared_context import SharedContext, SubTask, TaskStatus
from .lead_agent import LeadAgent
from .events import Event, EventType
from mediZJ.agents import ConsultationAgent, DiagnosticAgent, ResearchAgent
from mediZJ.memory import SessionSummaryManager, SessionSummary, ShortTermMemory, LongTermMemory, PersonalProfile

# Trace 惰性导入
try:
    from mediZJ.trace.context import traced_span
    from mediZJ.trace.models import SpanType, AgentAttributes
    _TRACE = True
except ImportError:
    _TRACE = False

# LangGraph 双轨开关
_USE_LANGGRAPH = os.environ.get("MEDIZJ_USE_LANGGRAPH", "true").lower() in ("true", "1", "yes")
_LANGGRAPH_AVAILABLE = False
try:
    from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
    from mediZJ.lgraph.tool_registry import ToolRegistry
    from mediZJ.core.skill_loader import discover_skills, invalidate_skill_cache
    from mediZJ.core.skill_models import SkillDefinition
    _LANGGRAPH_AVAILABLE = True
except ImportError as e:
    if _USE_LANGGRAPH:
        logger.warning(f"MEDIZJ_USE_LANGGRAPH=true 但 langgraph 模块不可用: {e}")


# 进程级共享熔断器（跨请求累计 LLM 失败次数）
_shared_circuit_breaker = None


def _get_shared_circuit_breaker():
    """获取进程级共享熔断器实例"""
    global _shared_circuit_breaker
    if _shared_circuit_breaker is None:
        from mediZJ.core.circuit_breaker import CircuitBreaker
        _shared_circuit_breaker = CircuitBreaker(
            failure_threshold=5, cooldown_seconds=30.0
        )
    return _shared_circuit_breaker


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
        questionnaire_manager: Optional[Any] = None,
        user_id: Optional[str] = None
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
        self.long_term_memory = LongTermMemory(user_id=user_id or "default")
        self.personal_profile = PersonalProfile(user_id=user_id or "default")
        self.ltm_save_task = None

        # 熔断器：进程级共享实例，跨请求累计失败（连续 5 次 LLM 错误后拒绝新请求 30s）
        self.cb = _get_shared_circuit_breaker()

        # LangGraph ToolRegistry（双轨模式下使用）
        self._tool_registry = None
        if _USE_LANGGRAPH and _LANGGRAPH_AVAILABLE:
            self._init_langgraph_registry()

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

    # ===== LangGraph 双轨模式方法 =====

    def _init_langgraph_registry(self):
        """初始化 LangGraph ToolRegistry（从 .claude/skills/ 加载）"""
        if not _LANGGRAPH_AVAILABLE:
            return

        from pathlib import Path
        # __file__ = mediZJ/swarm/swarm_coordinator.py
        # parent = mediZJ/swarm/, parent.parent = mediZJ/
        # parent.parent.parent = 项目根目录
        project_root = Path(__file__).parent.parent.parent
        project_root = project_root.resolve()

        discovered = discover_skills(project_root)
        if not discovered:
            logger.warning("未发现任何 Skill，LangGraph 模式将只有基础工具")
            return

        self._tool_registry = ToolRegistry()
        self._tool_registry.register_from_skills(discovered)

        # 注册基础工具：activate_skill
        from mediZJ.core.tools.activate_skill import create_activate_skill_tool

        # 使用 ToolRegistry 自己的 activate_skill 逻辑
        async def _activate_skill(name: str) -> Dict[str, Any]:
            """激活指定 Skill，使其工具对 LLM 可见"""
            skill_names = self._tool_registry.get_skill_names()
            if name not in skill_names:
                return {
                    "success": False,
                    "error": f"未知 Skill: {name}。可用 Skills: {', '.join(skill_names)}",
                }
            instructions = self._tool_registry.get_skill_instructions(name)
            tool_names = self._tool_registry.get_skill_tool_names(name)
            return {
                "success": True,
                "active_skill": name,
                "instructions": instructions or "",
                "description": f"Skill '{name}' 已激活，{len(tool_names)} 个工具可用",
                "available_tools": tool_names,
            }

        self._tool_registry.register_base_tool(
            name="activate_skill",
            func=_activate_skill,
            description="激活指定 Skill。激活后可以使用该 Skill 的工具。同一时间只能有一个 Skill 处于激活状态。",
        )

        # 注册基础工具：question_for_user
        from mediZJ.core.tools.questionnaire import create_question_for_user_tool

        def _get_manager():
            return self.questionnaire_manager

        q_func = create_question_for_user_tool(_get_manager)
        self._tool_registry.register_base_tool(
            name="question_for_user",
            func=q_func,
            description="向用户发送结构化问卷，收集诊断所需信息。",
        )

        logger.info(
            f"[LangGraph] ToolRegistry initialized: "
            f"{len(self._tool_registry)} tools, "
            f"{len(self._tool_registry.get_skill_names())} skills"
        )

    async def _process_with_langgraph(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        使用 LangGraph SupervisorGraph 处理请求（双轨模式）

        Args:
            question: 用户问题
            context: 额外上下文
            session_id: 会话ID

        Returns:
            与 process() 相同格式的结果
        """
        if not _LANGGRAPH_AVAILABLE or self._tool_registry is None:
            logger.warning("LangGraph 不可用，回退到 legacy 路径")
            return await self._process_legacy(question, context, session_id)

        start_time = datetime.now()
        if session_id is None:
            session_id = f"{start_time.strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

        logger.info(f"[LangGraph] Processing (session={session_id}): {question[:300]}{'...' if len(question) > 300 else ''}")

        # 构建并执行 SupervisorGraph
        graph = build_supervisor_graph(
            coordinator=self,
            tool_registry=self._tool_registry,
            event_callback=self.event_callback,
        )

        config = {"configurable": {"thread_id": session_id}}

        initial_state = {
            "question": question,
            "session_id": session_id,
            "context": context or {},
            "start_time": start_time.isoformat(),
            "clarify_complete": False,
            "clarify_round": 0,
            "subtasks": [],
            "swarm_contributions": {},
            "swarm_subtasks_status": {},
            "all_references": {},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "_swarm_finalized": False,
        }

        try:
            result_state = await graph.ainvoke(initial_state, config)
        except Exception as e:
            logger.error(f"[LangGraph] 图执行异常: {e}")
            return {
                "answer": f"系统处理异常: {e}",
                "session_id": session_id,
                "swarm_enabled": False,
                "agents_involved": [],
                "error": str(e),
            }

        end_time = datetime.now()

        # 从图 state 构建返回结果（与 legacy 格式兼容）
        result = {
            "answer": result_state.get("final_answer", ""),
            "suggestions": result_state.get("suggestions", []),
            "disclaimer": result_state.get("disclaimer",
                "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"),
            "session_id": session_id,
            "swarm_enabled": result_state.get("swarm_enabled", False),
            "agents_involved": result_state.get("agents_involved", []),
            "subtasks_completed": len(result_state.get("swarm_contributions", {})),
            "total_time": result_state.get("total_time", (end_time - start_time).total_seconds()),
            "swarm_metadata": result_state.get("swarm_metadata", {}),
            "timeout_occurred": result_state.get("timeout_occurred", False),
            "usage": result_state.get("usage", {}),
            "citations": result_state.get("citations", []),
            "mode": result_state.get("mode", "langgraph"),
            "_swarm_finalized": True,
        }

        # LTM fire-and-forget
        ltm_task = asyncio.ensure_future(self._save_long_term_memory(
            session_id=session_id,
            question=question,
            answer=result["answer"],
            metadata={
                "mode": result_state.get("mode", "langgraph"),
                "subtasks_count": len(result_state.get("subtasks", [])),
                "total_time": result["total_time"],
                "total_tokens": result["usage"].get("total_tokens", 0),
            },
        ))
        result["_ltm_save_task"] = ltm_task

        logger.info(
            f"[LangGraph] 处理完成: mode={result['mode']}, "
            f"agents={result['agents_involved']}, "
            f"time={result['total_time']:.1f}s"
        )
        return result

    # _process_impl 是原有的 process() 逻辑，已重命名为 _process_impl
    # _process_with_langgraph 是新的 LangGraph 路径

    async def _retrieve_memories(self, session_id: str, question: str):
        """并行检索短期记忆和长期记忆

        Returns:
            (recent_history, similar_memories)，任一失败返回空列表降级
        """
        results = await asyncio.gather(
            self.short_term_memory.get_recent_messages(
                session_id=session_id,
                limit=10,  # 最近5轮对话（10条消息）
            ),
            self.long_term_memory.search_similar_sessions(
                query=question,
                limit=3,
            ),
            return_exceptions=True,
        )

        recent_history = results[0] if not isinstance(results[0], BaseException) else []
        similar_memories = results[1] if not isinstance(results[1], BaseException) else []

        if isinstance(results[0], BaseException):
            logger.warning(f"Short-term memory retrieval failed: {results[0]}")
        if isinstance(results[1], BaseException):
            logger.warning(f"Long-term memory retrieval failed: {results[1]}")

        return recent_history, similar_memories

    async def process(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """处理用户问题

        Pipeline: 检索 → 澄清 → 分解 → 路由 → 收尾

        双轨运行：
        - MEDIZJ_USE_LANGGRAPH=false（默认）：Legacy AgentLoop + asyncio.wait
        - MEDIZJ_USE_LANGGRAPH=true：LangGraph SupervisorGraph + Send API
        """
        # 双轨路由
        if _USE_LANGGRAPH and _LANGGRAPH_AVAILABLE and self._tool_registry is not None:
            return await self._process_with_langgraph(question, context, session_id)

        # Legacy 路径（原有逻辑不变）
        return await self._process_impl(question, context, session_id)

    async def _process_impl(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Legacy 处理实现（原有 process() 逻辑完整保留）"""
        start_time = datetime.now()
        if session_id is None:
            session_id = f"{start_time.strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

        logger.info(f"Processing question (session={session_id}): {question[:50]}...")

        try:
            # Step 0: 初始化 Trace
            _trace_collector = self._init_trace(session_id)

            # Step 1: 并行检索记忆 + 刷新 Worker 档案
            recent_history, similar_memories = await self._retrieve_memories(
                session_id=session_id, question=question,
            )
            self._refresh_worker_profiles()

            # Step 2: 构建增强上下文
            enhanced_context = self._build_enhanced_context(
                context or {}, recent_history, similar_memories,
            )

            # Step 3: 信息澄清 + 任务分解
            clarify_result = await self._do_clarify(question, enhanced_context, session_id)
            if clarify_result.get("clarified"):
                enhanced_context["collected_info"] = clarify_result["collected_info"]
                logger.info(f"LeadAgent 澄清完成，收集信息: {clarify_result['collected_info'][:100]}...")
            elif clarify_result.get("timeout_skipped"):
                logger.warning("信息澄清阶段超时跳过，继续任务分解")

            assessment = await self._do_assess_decompose(question, enhanced_context)
            subtasks = assessment.get("subtasks", [])
            logger.info(f"LeadAgent 分解任务：{len(subtasks)} 个")

            # Step 4: 路由并执行
            result = await self._route_and_execute(
                subtasks=subtasks, question=question,
                enhanced_context=enhanced_context,
                session_id=session_id, start_time=start_time,
            )

            # Step 5: 统一收尾（单Agent/Fallback 模式；Swarm 已在内部完成）
            result = await self._finalize(
                result=result, question=question, session_id=session_id,
                start_time=start_time, subtasks_count=len(subtasks),
                _trace_collector=_trace_collector,
            )
            return result

        except asyncio.CancelledError:
            logger.info(f"process() cancelled for session {session_id}")
            raise

    def _init_trace(self, session_id: str):
        """惰性初始化 Trace 收集器"""
        try:
            from mediZJ.trace.collector import TraceCollector
            from mediZJ.trace.storage import TraceSqliteStorage
            from mediZJ.trace.context import _current_trace_id

            collector = TraceCollector()
            collector.begin_trace(session_id)
            if not hasattr(collector, '_storage_set') or not collector._storage_set:
                collector.set_storage(TraceSqliteStorage())
                collector._storage_set = True
            _current_trace_id.set(session_id)
            logger.debug(f"[Trace] Started for session={session_id}")
            return collector
        except ImportError:
            return None

    def _refresh_worker_profiles(self):
        """刷新所有 Worker Agent 的用户档案"""
        personal_text = self.personal_profile.to_text()
        if personal_text != "暂无":
            for worker in self.worker_pool:
                if hasattr(worker, 'loop'):
                    worker.loop.user_context = personal_text

    def _build_enhanced_context(
        self, context: Dict, recent_history: List, similar_memories: List,
    ) -> Dict:
        """构建增强上下文（记忆 + 档案注入）"""
        enhanced = context

        lead_personal = self.personal_profile.to_text()
        if lead_personal != "暂无":
            enhanced["personal_profile"] = lead_personal

        if recent_history:
            enhanced["recent_history"] = [
                {"role": msg.get("role", ""), "content": msg.get("content", "")}
                for msg in recent_history
            ]
            logger.info(f"Loaded {len(recent_history)} recent messages from short-term memory")

        if similar_memories:
            enhanced["historical_cases"] = [
                {"summary": mem["content"], "score": mem["score"]}
                for mem in similar_memories
            ]
            logger.info(f"Found {len(similar_memories)} similar historical cases from long-term memory")

        return enhanced

    async def _do_clarify(
        self, question: str, enhanced_context: Dict, session_id: str,
    ) -> Dict:
        """执行信息澄清阶段（含 Trace span）"""
        _ctx = traced_span(SpanType.STAGE, name="clarify") if _TRACE else None
        if _ctx: _ctx.__enter__()
        try:
            return await self.lead_agent.clarify(
                question=question, context=enhanced_context,
                session_id=session_id, event_callback=self.event_callback,
                clarify_timeout=30.0,
            )
        finally:
            if _ctx: _ctx.__exit__(None, None, None)

    async def _do_assess_decompose(
        self, question: str, enhanced_context: Dict,
    ) -> Dict:
        """执行任务分解（含 Trace span）"""
        # 注入 LeadAgent thinking 回调
        if self.event_callback:
            def on_think(content, iteration):
                self.event_callback(Event(
                    type=EventType.AGENT_THINKING,
                    source_agent="lead_agent",
                    data={"content": content, "iteration": iteration},
                ))

            def on_think_done(iteration, elapsed_seconds):
                self.event_callback(Event(
                    type=EventType.AGENT_THINKING_DONE,
                    source_agent="lead_agent",
                    data={"iteration": iteration, "elapsed_seconds": elapsed_seconds},
                ))

            self.lead_agent.set_on_thinking(on_think)
            self.lead_agent.set_on_thinking_done(on_think_done)

        _ctx = traced_span(SpanType.STAGE, name="assess_decompose") if _TRACE else None
        if _ctx: _ctx.__enter__()
        try:
            return await self.lead_agent.assess_and_decompose(question, enhanced_context)
        finally:
            if _ctx: _ctx.__exit__(None, None, None)
            # 清理分解阶段的 thinking 回调，避免残留
            self.lead_agent.set_on_thinking(None)
            self.lead_agent.set_on_thinking_done(None)

    # ---- 记忆检索（P0 已抽取）----

    async def _flush_trace(self, collector, session_id, question, mode, result):
        """持久化 Trace 数据"""
        if collector is None:
            return
        try:
            from mediZJ.trace.models import TraceAttributes
            agents = result.get('agents_involved', []) if isinstance(result, dict) else []
            usage = result.get('usage', {}) if isinstance(result, dict) else {}
            tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0
            q = question[:200] if question else ''

            root_spans = collector.get_flat_spans(session_id)
            for s in (root_spans or []):
                if s.span_type.value == 'trace':
                    s.trace_attrs = TraceAttributes(
                        session_id=session_id,
                        user_id=self.personal_profile.user_id,
                        mode=mode or '',
                        question_summary=q, agents_involved=agents,
                        total_tokens=tokens,
                    )
                    break
            await collector.flush(session_id)
        except Exception as e:
            logger.warning(f"[Trace] Flush failed: {e}")

    # ===== 路由与收尾方法（P1-6/7 重构）=====

    async def _route_and_execute(
        self,
        subtasks: List[Dict],
        question: str,
        enhanced_context: Dict,
        session_id: str,
        start_time: datetime,
    ) -> Dict[str, Any]:
        """根据子任务数量路由到单 Agent / Swarm / Fallback 分支

        每个分支返回标准化的 result dict，包含 answer/disclaimer/suggestions 等统一字段。
        """
        if len(subtasks) == 1:
            # ---- 单 Agent 模式 ----
            task = subtasks[0]
            agent_id = task.get("assigned_agent")
            agent = self._get_agent_by_id(agent_id)
            if agent is None:
                logger.warning(f"Unknown agent_id: {agent_id}, fallback to ConsultationAgent")
                agent = self.consultation_agent
                agent_id = self.consultation_agent.agent_id

            logger.info(f"Route: Single Agent ({agent_id})")
            publish_fn = (lambda event: self.event_callback(event)) if self.event_callback else None

            subtask_obj = SubTask(
                id=task.get("id", "single"),
                type=task.get("type", "general"),
                description=task.get("description", question),
                assigned_agent=agent_id,
                status=TaskStatus.PENDING,
            )
            sub_session_id = f"{session_id}:{agent_id}:{subtask_obj.id}"

            branch_data = await self._execute_branch_with_agent(
                agent=agent, subtask=subtask_obj,
                session_id=session_id, sub_session_id=sub_session_id,
                question=question, agent_id=agent_id, publish_fn=publish_fn,
            )
            self._save_session_summary(
                session_id=session_id, question=question, agent_id=agent_id,
                final_answer=branch_data['final_answer'],
                start_time=start_time, usage=branch_data['token_usage'],
                message_count=branch_data['message_count'],
            )

            result = branch_data['result']
            # 将 AgentLoop 收集的 references 映射为 citations
            result['citations'] = result.pop('references', [])

            # 程序化追加参考资料章节
            if result['citations']:
                ref_section = self._format_references_section(result['citations'])
                if ref_section and result.get('answer'):
                    result['answer'] += "\n" + ref_section

            result.update({
                'swarm_enabled': False,
                'session_id': session_id,
                'route_reason': f'单任务路由到 {agent_id}',
                'usage': branch_data['token_usage'],
                'agents_involved': [agent_id],
                'mode': 'single_agent',
            })
            # 确保字段一致性
            if 'disclaimer' not in result:
                result['disclaimer'] = "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"
            if 'suggestions' not in result:
                result['suggestions'] = []

            return result

        elif len(subtasks) >= 2:
            # ---- Swarm 模式 ----
            logger.info(f"Route: Swarm (Multi-Agent Collaboration) - {len(subtasks)} tasks")
            self.ltm_save_task = None
            result = await self._process_with_swarm(
                question=question, context=enhanced_context,
                assessment={"subtasks": subtasks},
                session_id=session_id, start_time=start_time,
            )
            # _process_with_swarm 内部已包含所有后置处理并返回完整 result
            return result

        else:
            # ---- Fallback 降级模式 ----
            logger.warning("No subtasks generated, fallback to ConsultationAgent")
            agent = self.consultation_agent
            agent_id = agent.agent_id
            publish_fn = (lambda event: self.event_callback(event)) if self.event_callback else None

            fallback_subtask = SubTask(
                id="fallback", type="general", description=question,
                assigned_agent=agent_id, status=TaskStatus.PENDING,
            )
            sub_session_id = f"{session_id}:{agent_id}:fallback"

            branch_data = await self._execute_branch_with_agent(
                agent=agent, subtask=fallback_subtask,
                session_id=session_id, sub_session_id=sub_session_id,
                question=question, agent_id=agent_id, publish_fn=publish_fn,
            )
            self._save_session_summary(
                session_id=session_id, question=question, agent_id=agent_id,
                final_answer=branch_data['final_answer'],
                start_time=start_time, usage=branch_data['token_usage'],
                message_count=branch_data['message_count'],
            )

            result = branch_data['result']
            # 将 AgentLoop 收集的 references 映射为 citations
            result['citations'] = result.pop('references', [])

            # 程序化追加参考资料章节
            if result['citations']:
                ref_section = self._format_references_section(result['citations'])
                if ref_section and result.get('answer'):
                    result['answer'] += "\n" + ref_section

            result.update({
                'swarm_enabled': False,
                'session_id': session_id,
                'route_reason': '无可用子任务，降级到 ConsultationAgent',
                'usage': branch_data['token_usage'],
                'agents_involved': [agent_id],
                'mode': 'fallback',
            })
            if 'disclaimer' not in result:
                result['disclaimer'] = "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"
            if 'suggestions' not in result:
                result['suggestions'] = []

            return result

    async def _finalize(
        self,
        result: Dict[str, Any],
        question: str,
        session_id: str,
        start_time: datetime,
        subtasks_count: int,
        _trace_collector,
    ) -> Dict[str, Any]:
        """统一收尾：记忆保存 + LTM fire-and-forget + trace flush

        适用于单 Agent 和 Fallback 模式；Swarm 模式已在 _process_with_swarm 内部完成收尾，
        通过 _swarm_finalized 标记跳过。
        """
        if result.get('_swarm_finalized'):
            return result

        end_time = datetime.now()
        mode = result.get('mode', 'single_agent')
        usage = result.get('usage', {})
        final_answer = result.get('answer', '')
        total_tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

        result['total_time'] = (end_time - start_time).total_seconds()

        self.ltm_save_task = asyncio.ensure_future(self._save_long_term_memory(
            session_id=session_id, question=question, answer=final_answer,
            metadata={
                "mode": mode, "subtasks_count": subtasks_count,
                "total_time": result['total_time'],
                "total_tokens": total_tokens,
            }
        ))
        result['_ltm_save_task'] = self.ltm_save_task

        await self._flush_trace(_trace_collector, session_id, question, mode, result)
        return result

    # ===== Swarm 协作核心 =====

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

        # 附加 SharedContext 到所有 Worker（thinking 回调由 _worker_execute_assigned_tasks 中的 callback_scope 管理）
        for worker in self.worker_pool:
            worker.attach_shared_context(shared_context)

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
        task_to_worker = {}  # task → worker 映射，超时时用于检查问卷状态
        for worker in self.worker_pool:
            task = asyncio.create_task(
                self._worker_execute_assigned_tasks(worker, shared_context)
            )
            tasks.append(task)
            task_to_worker[task] = worker

        # 等待所有 Worker 完成（或超时）
        timeout_occurred = False
        done, pending = await asyncio.wait(tasks, timeout=90.0)
        timeout_occurred = len(pending) > 0

        # 检查已完成的 Worker 是否有异常
        for completed_task in done:
            exc = completed_task.exception()
            if exc:
                logger.error(f"Worker task failed with exception: {exc}")

        if timeout_occurred:
            logger.warning(f"Swarm execution timeout (90s), {len(pending)}/{len(tasks)} workers incomplete")
            for task in pending:
                worker = task_to_worker.get(task)
                if worker and hasattr(worker, 'loop'):
                    qm = worker.loop.questionnaire_manager
                    if qm and qm.has_pending:
                        # 取消待处理的问卷，让 Worker 自然超时返回部分结果
                        # 同时通知前端关闭问卷窗口
                        for qid in qm.pending_ids:
                            logger.info(f"取消问卷 {qid}（Swarm 超时），worker={worker.agent_id}")
                            qm.cancel(qid)
                            shared_context.publish_event(Event(
                                type=EventType.AGENT_QUESTIONNAIRE_CANCELLED,
                                source_agent="swarm_coordinator",
                                data={
                                    "questionnaire_id": qid,
                                    "reason": "swarm_timeout",
                                }
                            ))
                # 取消任务（如果问卷已被取消，Worker 会快速返回；否则暴力取消）
                task.cancel()
            # 等待已取消的 Worker 返回中间结果（CancelledError 处理会在 AgentLoop 中写入部分结果）
            for task in pending:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            completed_agents = list(shared_context.agent_contributions.keys())
            claimed_tasks = [
                (subtask.assigned_to, subtask.type)
                for subtask in shared_context.task_decomposition.values()
                if subtask.status.value == "claimed"
            ]
            logger.info(f"Completed agents after timeout: {completed_agents}")
            logger.info(f"Timed out tasks: {claimed_tasks}")

        # Step 2.5: 收集并统一所有 Worker 的知识库引用
        # 跨 Worker 按 doc_id 去重，重新编号，替换贡献文本中的引用编号
        swarm_citations, renumber_map = self._unify_swarm_references(shared_context)
        if renumber_map:
            # 替换各贡献文本中的引用编号为新编号
            self._apply_renumber_map(shared_context, renumber_map)

        # Step 3: LeadAgent 汇总结果
        # 即使超时，也尝试汇总已完成的部分结果
        # 注入 thinking 回调（synthesize 阶段）
        if self.event_callback:
            def on_think_synth(content, iteration):
                self.event_callback(Event(
                    type=EventType.AGENT_THINKING,
                    source_agent="lead_agent",
                    data={"content": content, "iteration": iteration},
                ))

            def on_think_done_synth(iteration, elapsed_seconds):
                self.event_callback(Event(
                    type=EventType.AGENT_THINKING_DONE,
                    source_agent="lead_agent",
                    data={"iteration": iteration, "elapsed_seconds": elapsed_seconds},
                ))

            self.lead_agent.set_on_thinking(on_think_synth)
            self.lead_agent.set_on_thinking_done(on_think_done_synth)

        final_answer = await self.lead_agent.synthesize_results(
            question=question,
            shared_context=shared_context,
            timeout_occurred=timeout_occurred
        )

        # 程序化追加参考资料章节（不依赖 LLM 生成）
        if swarm_citations:
            ref_section = self._format_references_section(swarm_citations)
            if ref_section:
                final_answer += "\n" + ref_section

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
            'citations': swarm_citations,
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
        result['_ltm_save_task'] = self.ltm_save_task

        result['_swarm_finalized'] = True  # 标记已在 _process_with_swarm 内完成收尾
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
        - 在 callback_scope 内执行（自动注入/清理 thinking 回调）
        - 记录结果
        """
        async with self.callback_scope(worker, shared_context.publish_event):
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
        _ctx = traced_span(SpanType.AGENT, name=worker.agent_id) if _TRACE else None
        if _ctx:
            _ctx.__enter__()
            _ctx.span.agent_attrs = AgentAttributes(
                agent_id=worker.agent_id, subtask_id=subtask.id, subtask_type=subtask.type
            )
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

            # 回填 Agent trace 属性
            if _ctx and _ctx.span.agent_attrs:
                _ctx.span.agent_attrs.iteration_count = result.get('iterations', 0)
                if hasattr(worker, 'loop') and hasattr(worker.loop, 'tool_call_count'):
                    _ctx.span.agent_attrs.tool_call_count = worker.loop.tool_call_count
                if result.get('usage'):
                    _ctx.span.agent_attrs.total_tokens = result['usage'].get('total_tokens', 0)
        except Exception as e:
            logger.error(f"{worker.agent_id}: Error in {subtask.type}: {e}")
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)

    async def _execute_single_agent_traced(self, agent, subtask, session_id, sub_session_id):
        """单Agent/降级模式：带 AGENT span 执行子任务

        非 Swarm 路径也需要创建 AGENT span，使 trace 中 ITERATION span
        正确归属到 AGENT 下，并提供 agent_attrs（iteration_count 等）。
        """
        _ctx = traced_span(SpanType.AGENT, name=agent.agent_id) if _TRACE else None
        if _ctx:
            _ctx.__enter__()
            _ctx.span.agent_attrs = AgentAttributes(
                agent_id=agent.agent_id, subtask_id=subtask.id, subtask_type=subtask.type
            )
        try:
            logger.info(
                f"{agent.agent_id}: Executing {subtask.type} "
                f"(sub_session={sub_session_id})"
            )

            result = await agent.process_subtask(
                subtask,
                session_id=session_id,
                sub_session_id=sub_session_id,
            )
            logger.info(f"{agent.agent_id}: Completed {subtask.type}")

            # 回填 Agent trace 属性
            if _ctx and _ctx.span.agent_attrs:
                _ctx.span.agent_attrs.iteration_count = result.get('iterations', 0)
                if hasattr(agent, 'loop') and hasattr(agent.loop, 'tool_call_count'):
                    _ctx.span.agent_attrs.tool_call_count = agent.loop.tool_call_count
                if result.get('usage'):
                    _ctx.span.agent_attrs.total_tokens = result['usage'].get('total_tokens', 0)
            return result
        except Exception as e:
            logger.error(f"{agent.agent_id}: Error in {subtask.type}: {e}")
            raise
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)

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
                    max_tokens=2048,
                    response_format={'type': 'json_object'},
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Memory eval LLM timeout (60s), session={session_id}")
            return None
        except Exception as e:
            logger.warning(f"Memory eval LLM failed: {e}, session={session_id}")
            return None

        # 4. 解析 JSON（response_format 已保证合法 JSON 输出）
        try:
            result = json.loads(response)
        except json.JSONDecodeError as e:
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

    # --- Thinking 回调上下文管理器 ---

    @asynccontextmanager
    async def callback_scope(self, worker, publish_fn):
        """注入 thinking 回调的上下文管理器，退出时自动清理

        Usage:
            async with self.callback_scope(worker, publish_fn):
                await worker.process_subtask(...)
        """
        if not publish_fn or not hasattr(worker, 'set_on_thinking'):
            yield
            return
        try:
            self._inject_thinking_callbacks(worker, publish_fn)
            yield
        finally:
            self._cleanup_thinking_callbacks(worker)

    # --- 分支执行共用方法（P1-7 去重）---

    async def _execute_branch_with_agent(
        self, agent, subtask, session_id, sub_session_id,
        question, agent_id, publish_fn,
    ):
        """执行单个 Agent 分支（单Agent / Fallback 模式共用）

        封装：callback_scope → execute → extract → STM 保存
        """
        async with self.callback_scope(agent, publish_fn):
            result = await self._execute_single_agent_traced(
                agent, subtask, session_id, sub_session_id
            )

        final_answer = result.get('answer', '')
        token_usage = result.get('usage', {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
        message_count = result.get('message_count', 0)

        # 将用户问题和子会话结果合并回主会话
        await self.short_term_memory.add_message(
            session_id=session_id, role="user", content=question,
        )
        self.short_term_memory.merge_sub_session(
            main_session_id=session_id,
            sub_session_id=sub_session_id,
            summary_text=final_answer if final_answer else "",
        )

        return {
            'final_answer': final_answer,
            'token_usage': token_usage,
            'message_count': message_count,
            'result': result,
        }

    def _save_session_summary(
        self, session_id, question, agent_id, final_answer,
        start_time, usage, message_count,
    ):
        """保存单 Agent / Fallback 模式的 SessionSummary"""
        try:
            end_time = datetime.now()
            summary = SessionSummary.from_single_agent(
                session_id=session_id, question=question, agent_id=agent_id,
                final_answer=final_answer, start_time=start_time, end_time=end_time,
                usage=usage, total_messages=message_count,
            )
            self.session_manager.save_summary(summary)
        except Exception as e:
            logger.error(f"Failed to save session summary: {e}")

    # --- Swarm 引用统一 ---

    def _unify_swarm_references(self, shared_context: "SharedContext") -> tuple:
        """
        收集所有 Worker 贡献中的知识库引用，按 doc_id 去重后重新编号。

        Returns:
            (citations, renumber_map): citations 为统一后的引用列表，
            renumber_map 为 {agent_id: {old_index: new_index}} 映射
        """
        # Step 1: 收集所有 Worker 的 references
        all_refs: Dict[str, Dict] = {}  # doc_id -> ref
        agent_ref_map: Dict[str, List[Dict]] = {}  # agent_id -> [(old_index, doc_id)]

        for agent_id, contributions in shared_context.agent_contributions.items():
            agent_refs = []
            for contrib in contributions:
                refs = contrib.result.get("references", [])
                for ref in refs:
                    doc_id = ref.get("doc_id", "")
                    if doc_id and doc_id not in all_refs:
                        all_refs[doc_id] = ref
                    if doc_id:
                        agent_refs.append({"old_index": ref.get("index", 0), "doc_id": doc_id})
            if agent_refs:
                agent_ref_map[agent_id] = agent_refs

        if not all_refs:
            return [], {}

        # Step 2: 按原始 index 排序后重新编号
        sorted_refs = sorted(all_refs.values(), key=lambda r: r.get("index", 0))
        citations = []
        doc_to_new_index: Dict[str, int] = {}
        for new_idx, ref in enumerate(sorted_refs, 1):
            ref_copy = dict(ref)
            ref_copy["index"] = new_idx
            citations.append(ref_copy)
            doc_to_new_index[ref.get("doc_id", "")] = new_idx

        # Step 3: 构建 old_index -> new_index 映射（per agent）
        renumber_map: Dict[str, Dict[int, int]] = {}
        for agent_id, refs in agent_ref_map.items():
            mapping = {}
            for r in refs:
                old_idx = r["old_index"]
                doc_id = r["doc_id"]
                new_idx = doc_to_new_index.get(doc_id, old_idx)
                mapping[old_idx] = new_idx
            renumber_map[agent_id] = mapping

        return citations, renumber_map

    def _apply_renumber_map(self, shared_context: "SharedContext", renumber_map: Dict[str, Dict[int, int]]):
        """将各 Worker 贡献文本中的旧引用编号替换为新编号"""
        citation_pattern = re.compile(r'\[(\d+(?:[,\-]\d+)*)\]')

        for agent_id, mapping in renumber_map.items():
            if not mapping:
                continue
            contributions = shared_context.agent_contributions.get(agent_id, [])
            for contrib in contributions:
                if not isinstance(contrib.result, dict):
                    continue
                answer = contrib.result.get("answer", "")
                if not answer:
                    continue

                def replace_ref(match):
                    nums_str = match.group(1)
                    parts = re.split(r'([,\-])', nums_str)
                    new_parts = []
                    for part in parts:
                        if part in (',', '-'):
                            new_parts.append(part)
                        else:
                            try:
                                old_num = int(part)
                                new_num = mapping.get(old_num, old_num)
                                new_parts.append(str(new_num))
                            except ValueError:
                                new_parts.append(part)
                    return '[' + ''.join(new_parts) + ']'

                contrib.result["answer"] = citation_pattern.sub(replace_ref, answer)

    # --- 建议提取 ---

    @staticmethod
    def _format_references_section(citations: list) -> str:
        """将引用列表格式化为 ## 参考资料 Markdown 章节"""
        if not citations:
            return ""

        lines = ["", "## 参考资料", ""]
        for ref in citations:
            index = ref.get("index", "")
            filename = ref.get("filename", "")

            if filename:
                lines.append(f"[{index}] {filename}")
            else:
                lines.append(f"[{index}]")

            lines.append("")

        return "\n".join(lines)

    def _extract_suggestions(self, final_answer: str) -> List[str]:
        """从最终答案中提取建议（简化实现）"""
        suggestions = []

        # 匹配 ## 核心建议 章节（兼容【核心建议】旧格式）
        if "## 核心建议" in final_answer or "【核心建议】" in final_answer:
            # 查找章节起始位置
            start_marker = "## 核心建议" if "## 核心建议" in final_answer else "【核心建议】"
            start_idx = final_answer.find(start_marker) + len(start_marker)
            # 查找下一个 ## 标题作为结束边界
            end_match = re.search(r'\n## ', final_answer[start_idx:])
            if end_match:
                end_idx = start_idx + end_match.start()
            else:
                end_idx = len(final_answer)

            suggestions_text = final_answer[start_idx:end_idx]

            # 提取编号列表
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
