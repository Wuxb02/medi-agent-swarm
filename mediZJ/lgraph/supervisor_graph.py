"""
SupervisorGraph — 替代 SwarmCoordinator.process() 的 LangGraph 主图

将 process() 流水线映射为 LangGraph 状态图：
- retrieve_memories → clarify → assess_decompose → route
  - single → single_agent → finalize → END
  - swarm → send_workers (Send fan-out) → worker_executor → synthesize_results → finalize → END
  - fallback → single_agent → finalize → END

采用 Map-Reduce 模式：Send API 并行扇出 Worker，synthesize_results 汇总。
"""
import asyncio
import uuid
import re
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
from loguru import logger

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

from mediZJ.lgraph.supervisor_state import SupervisorState
from mediZJ.lgraph.agent_subgraph import build_agent_subgraph
from mediZJ.lgraph.tool_registry import ToolRegistry
from mediZJ.swarm.events import Event, EventType
from mediZJ.core.prompt_loader import PromptLoader

# Trace
try:
    from mediZJ.trace.context import traced_span
    from mediZJ.trace.models import SpanType
    TRACE_AVAILABLE = True
except ImportError:
    TRACE_AVAILABLE = False


def build_supervisor_graph(
    coordinator,                    # SwarmCoordinator 实例（持有所有 Agent、记忆管理器等）
    tool_registry: ToolRegistry,    # 共享的 ToolRegistry
    event_callback: Optional[Callable] = None,
) -> StateGraph:
    """
    构建主 SupervisorGraph

    Args:
        coordinator: SwarmCoordinator 实例
        tool_registry: 工具注册中心
        event_callback: 事件回调（用于流式 SSE 推送）

    Returns:
        编译后的 CompiledStateGraph
    """
    lead_agent = coordinator.lead_agent
    consultation_agent = coordinator.consultation_agent
    diagnostic_agent = coordinator.diagnostic_agent
    research_agent = coordinator.research_agent

    def _get_agent_by_id(agent_id: str):
        mapping = {
            "consultation_agent": consultation_agent,
            "diagnostic_agent": diagnostic_agent,
            "research_agent": research_agent,
        }
        return mapping.get(agent_id)

    # ===== 节点函数 =====

    async def _retrieve_memories(state: SupervisorState) -> dict:
        """节点: 并行检索短期记忆和长期记忆（替代 coordinator._retrieve_memories）"""
        session_id = state["session_id"]
        question = state["question"]

        results = await asyncio.gather(
            coordinator.short_term_memory.get_recent_messages(
                session_id=session_id, limit=10,
            ),
            coordinator.long_term_memory.search_similar_sessions(
                query=question, limit=3,
            ),
            return_exceptions=True,
        )

        recent_history = results[0] if not isinstance(results[0], BaseException) else []
        similar_memories = results[1] if not isinstance(results[1], BaseException) else []

        if isinstance(results[0], BaseException):
            logger.warning(f"短期记忆检索失败: {results[0]}")
        if isinstance(results[1], BaseException):
            logger.warning(f"长期记忆检索失败: {results[1]}")

        # 刷新 Worker 档案
        coordinator._refresh_worker_profiles()

        personal_text = coordinator.personal_profile.to_text()

        logger.info(
            f"[SupervisorGraph] 记忆检索完成: "
            f"recent={len(recent_history)}条, similar={len(similar_memories)}条"
        )

        return {
            "recent_history": recent_history,
            "similar_memories": similar_memories,
            "personal_profile": personal_text if personal_text != "暂无" else "",
        }

    async def _clarify(state: SupervisorState) -> dict:
        """节点: 信息澄清阶段（替代 coordinator._do_clarify）

        使用 LangGraph interrupt() 替代 asyncio.Future 问卷阻塞。
        """
        if not coordinator.questionnaire_manager:
            logger.debug("QuestionnaireManager 未配置，跳过澄清阶段")
            return {"clarify_complete": True, "collected_info": ""}

        # 构建增强上下文
        enhanced_context = {
            "personal_profile": state.get("personal_profile", ""),
            "recent_history": state.get("recent_history", []),
            "historical_cases": state.get("similar_memories", []),
        }

        # Trace: STAGE span
        _ctx = traced_span(SpanType.STAGE, name="clarify") if TRACE_AVAILABLE else None
        if _ctx:
            _ctx.__enter__()

        try:
            clarify_result = await lead_agent.clarify(
                question=state["question"],
                context=enhanced_context,
                session_id=state["session_id"],
                event_callback=event_callback,
                clarify_timeout=30.0,
            )
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)

        return {
            "clarify_complete": True,
            "collected_info": clarify_result.get("collected_info", ""),
            "clarify_answers": clarify_result.get("raw_answers", {}),
            "clarify_timeout_skipped": clarify_result.get("timeout_skipped", False),
        }

    async def _assess_decompose(state: SupervisorState) -> dict:
        """节点: 任务分解（替代 coordinator._do_assess_decompose）"""
        # 注入 LeadAgent thinking 回调
        if event_callback:
            def _on_think(content, iteration):
                event_callback(Event(
                    type=EventType.AGENT_THINKING,
                    source_agent="lead_agent",
                    data={"content": content, "iteration": iteration},
                ))

            def _on_think_done(iteration, elapsed_seconds):
                event_callback(Event(
                    type=EventType.AGENT_THINKING_DONE,
                    source_agent="lead_agent",
                    data={"iteration": iteration, "elapsed_seconds": elapsed_seconds},
                ))

            lead_agent.set_on_thinking(_on_think)
            lead_agent.set_on_thinking_done(_on_think_done)

        enhanced_context = {
            "personal_profile": state.get("personal_profile", ""),
            "recent_history": state.get("recent_history", []),
            "historical_cases": state.get("similar_memories", []),
            "collected_info": state.get("collected_info", ""),
        }

        _ctx = traced_span(SpanType.STAGE, name="assess_decompose") if TRACE_AVAILABLE else None
        if _ctx:
            _ctx.__enter__()

        try:
            assessment = await lead_agent.assess_and_decompose(
                question=state["question"],
                context=enhanced_context,
            )
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)
            lead_agent.set_on_thinking(None)
            lead_agent.set_on_thinking_done(None)

        subtasks = assessment.get("subtasks", [])
        logger.info(f"[SupervisorGraph] 任务分解完成: {len(subtasks)} 个子任务")

        return {
            "subtasks": subtasks,
        }

    async def _single_agent_node(state: SupervisorState) -> dict:
        """节点: 单 Agent 或 Fallback 模式执行（替代 coordinator._execute_branch_with_agent）

        包含 AgentSubGraph 的完整执行 + SessionSummary 保存 + citations 格式化。
        """
        subtasks = state.get("subtasks", [])
        if not subtasks:
            # Fallback
            task = {"type": "general", "description": state["question"],
                    "assigned_agent": "consultation_agent", "id": "fallback"}
        else:
            task = subtasks[0]

        agent_id = task.get("assigned_agent", "consultation_agent")
        agent = _get_agent_by_id(agent_id)
        if agent is None:
            agent = consultation_agent
            agent_id = agent.agent_id

        sub_session_id = f"{state['session_id']}:{agent_id}:{task.get('id', 'single')}"

        # 注入流式回调
        if event_callback:
            _inject_worker_callbacks(agent, agent_id, event_callback)

        # Trace: AGENT span
        _ctx = traced_span(SpanType.AGENT, name=agent_id) if TRACE_AVAILABLE else None
        if _ctx:
            _ctx.__enter__()

        try:
            # 构建并执行 AgentSubGraph
            subgraph = build_agent_subgraph(
                agent=agent,
                tool_registry=tool_registry,
                max_iterations=agent.config.get('max_iterations', 10),
                max_tool_calls=2,
                on_thinking=agent.loop.on_thinking,
                on_tool_step=agent.loop.on_tool_step,
                on_thinking_done=agent.loop.on_thinking_done,
                on_content_token=agent.loop.on_content_token,
                on_questionnaire=agent.loop.on_questionnaire,
            )

            result = await subgraph.ainvoke({
                "agent_id": agent_id,
                "sub_session_id": sub_session_id,
                "session_id": state["session_id"],
                "subtask_id": task.get("id", ""),
                "subtask_type": task.get("type", ""),
                "subtask_description": task.get("description", ""),
                "question": state["question"],  # 含图片分析文本的完整问题
                "max_iterations": agent.config.get('max_iterations', 10),
                "max_tool_calls": 2,
            })
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)
            if event_callback:
                _cleanup_worker_callbacks(agent)

        final_answer = result.get("final_answer", "")
        token_usage = result.get("usage", {})
        msg_count = result.get("message_count", 0)
        citations = result.get("references", [])

        # 保存 SessionSummary
        coordinator._save_session_summary(
            session_id=state["session_id"],
            question=state["question"],
            agent_id=agent_id,
            final_answer=final_answer,
            start_time=datetime.fromisoformat(state["start_time"])
                if state.get("start_time") else datetime.now(),
            usage=token_usage,
            message_count=msg_count,
        )

        # 程序化追加参考资料章节
        if citations:
            ref_section = _format_references_section(citations)
            if ref_section:
                final_answer += "\n" + ref_section

        # 子会话合并到主会话
        await coordinator.short_term_memory.add_message(
            session_id=state["session_id"], role="user",
            content=state["question"],
        )
        coordinator.short_term_memory.merge_sub_session(
            main_session_id=state["session_id"],
            sub_session_id=sub_session_id,
            summary_text=final_answer,
        )

        return {
            "final_answer": final_answer,
            "citations": citations,
            "usage": token_usage,
            "agents_involved": [agent_id],
            "swarm_enabled": False,
            "mode": "single_agent" if len(subtasks) == 1 else "fallback",
            "route_reason": (
                f"单任务路由到 {agent_id}" if len(subtasks) == 1
                else "无可用子任务，降级到 ConsultationAgent"
            ),
            "suggestions": _extract_suggestions(final_answer),
            "disclaimer": "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。",
        }

    def _send_workers_node(state: SupervisorState) -> dict:
        """节点: Send 扇出准备——不实际执行，仅做状态传递准备

        实际的扇出由 _fan_out_to_workers 条件边完成。
        """
        subtasks = state.get("subtasks", [])
        logger.info(f"[SupervisorGraph] Swarm Map-Reduce: {len(subtasks)} Workers")

        # 初始化 swarm 状态字段
        return {
            "swarm_contributions": {},
            "swarm_subtasks_status": {
                st.get("id", str(i)): "pending"
                for i, st in enumerate(subtasks)
            },
            "swarm_enabled": True,
            "mode": "swarm",
            "swarm_events": [],
        }

    async def _worker_executor_node(state: Dict) -> dict:
        """Send 目标节点: 每个 Worker 独立执行 AgentSubGraph（Map 阶段）"""
        agent_id = state.get("agent_id", "consultation_agent")
        agent = _get_agent_by_id(agent_id)
        if agent is None:
            agent = consultation_agent
            agent_id = agent.agent_id

        subtask = state.get("subtask", {})
        sub_session_id = state.get("sub_session_id", "")

        # 注入流式回调
        if event_callback:
            _inject_worker_callbacks(agent, agent_id, event_callback)

            # 发射 AGENT_START
            event_callback(Event(
                type=EventType.SUBTASK_STARTED,
                source_agent=agent_id,
                data={"subtask_id": subtask.get("id", ""), "type": subtask.get("type", "")},
            ))

        # Trace: AGENT span
        _ctx = traced_span(SpanType.AGENT, name=agent_id) if TRACE_AVAILABLE else None
        if _ctx:
            _ctx.__enter__()

        try:
            subgraph = build_agent_subgraph(
                agent=agent,
                tool_registry=tool_registry,
                max_iterations=agent.config.get('max_iterations', 10),
                max_tool_calls=2,
                on_thinking=agent.loop.on_thinking,
                on_tool_step=agent.loop.on_tool_step,
                on_thinking_done=agent.loop.on_thinking_done,
                on_content_token=agent.loop.on_content_token,
                on_questionnaire=agent.loop.on_questionnaire,
            )

            # Swarm 90s 超时
            result = await asyncio.wait_for(
                subgraph.ainvoke({
                    "agent_id": agent_id,
                    "sub_session_id": sub_session_id,
                    "session_id": state.get("session_id", ""),
                    "subtask_id": subtask.get("id", ""),
                    "subtask_type": subtask.get("type", ""),
                    "subtask_description": subtask.get("description", ""),
                    "question": state["question"],  # 含图片分析文本的完整问题
                    "max_iterations": agent.config.get('max_iterations', 10),
                    "max_tool_calls": 2,
                }),
                timeout=90.0,
            )

            timeout_occurred = False
        except asyncio.TimeoutError:
            logger.warning(f"Worker {agent_id} 超时 (90s)")
            result = {
                "final_answer": f"[{agent_id}] 分析超时，未能完成。",
                "references": [],
                "usage": {},
                "message_count": 0,
                "iterations": 0,
                "agent_id": agent_id,
            }
            timeout_occurred = True
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)
            if event_callback:
                _cleanup_worker_callbacks(agent)

        # 发射 AGENT_COMPLETE
        if event_callback:
            event_callback(Event(
                type=EventType.SUBTASK_COMPLETED,
                source_agent=agent_id,
                data={
                    "subtask_id": subtask.get("id", ""),
                    "answer_preview": result.get("final_answer", "")[:200],
                },
            ))

        return {
            "swarm_contributions": {
                agent_id: [{
                    "agent_id": agent_id,
                    "subtask_id": subtask.get("id", ""),
                    "result": {
                        "answer": result.get("final_answer", ""),
                        "references": result.get("references", []),
                        "usage": result.get("usage", {}),
                        "message_count": result.get("message_count", 0),
                        "iterations": result.get("iterations", 0),
                    },
                }]
            },
            "swarm_subtasks_status": {
                subtask.get("id", ""): "completed"
            },
            "timeout_occurred": timeout_occurred,
        }

    async def _synthesize_results(state: SupervisorState) -> dict:
        """节点: 综合 Worker 结果（Reduce 阶段，替代 coordinator 中的 synthesize + 引用统一 + 章节追加）"""
        contributions = state.get("swarm_contributions", {})
        session_id = state["session_id"]

        # 注入 thinking 回调
        if event_callback:
            def _on_think_synth(content, iteration):
                event_callback(Event(
                    type=EventType.AGENT_THINKING,
                    source_agent="lead_agent",
                    data={"content": content, "iteration": iteration},
                ))
            def _on_think_done_synth(iteration, elapsed_seconds):
                event_callback(Event(
                    type=EventType.AGENT_THINKING_DONE,
                    source_agent="lead_agent",
                    data={"iteration": iteration, "elapsed_seconds": elapsed_seconds},
                ))
            lead_agent.set_on_thinking(_on_think_synth)
            lead_agent.set_on_thinking_done(_on_think_done_synth)

        # 收集所有贡献的 answer 文本 + 引用统一（匹配原 swarm 的 _unify_swarm_references + _apply_renumber_map）
        completed_agents = []
        swarm_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        swarm_msg_count = 0

        # Step 1: 收集所有 Worker 的 references，按 doc_id 去重
        all_refs: Dict[str, Dict] = {}  # doc_id -> ref
        agent_ref_map: Dict[str, List[Dict]] = {}  # agent_id -> [(old_index, doc_id)]

        for agent_id, contribs in contributions.items():
            agent_refs = []
            for contrib in contribs:
                completed_agents.append(agent_id)
                # 累加 token
                u = contrib["result"].get("usage", {})
                swarm_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                swarm_usage["completion_tokens"] += u.get("completion_tokens", 0)
                swarm_usage["total_tokens"] += u.get("total_tokens", 0)
                swarm_msg_count += contrib["result"].get("message_count", 0)
                # 收集 references
                refs = contrib["result"].get("references", [])
                for ref in refs:
                    doc_id = ref.get("doc_id", "")
                    if doc_id and doc_id not in all_refs:
                        all_refs[doc_id] = ref
                    if doc_id:
                        agent_refs.append({"old_index": ref.get("index", 0), "doc_id": doc_id})
            if agent_refs:
                agent_ref_map[agent_id] = agent_refs

        # Step 2: 按原始 index 排序后重新编号
        sorted_refs = sorted(all_refs.values(), key=lambda r: r.get("index", 0))
        swarm_citations = []
        doc_to_new_index: Dict[str, int] = {}
        for new_idx, ref in enumerate(sorted_refs, 1):
            ref_copy = dict(ref)
            ref_copy["index"] = new_idx
            swarm_citations.append(ref_copy)
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

        # Step 4: 构建临时 SharedContext（先注入 Contribution，再 apply renumber）
        from mediZJ.swarm.shared_context import SharedContext
        shared_ctx = SharedContext(session_id=session_id)
        for agent_id, contribs in contributions.items():
            shared_ctx.agent_contributions[agent_id] = []
            from mediZJ.swarm.shared_context import Contribution
            for contrib in contribs:
                shared_ctx.agent_contributions[agent_id].append(Contribution(
                    agent_id=agent_id,
                    subtask_id=contrib.get("subtask_id", ""),
                    result=dict(contrib["result"]),  # 复制避免修改原数据
                ))

        # Step 5: 替换各贡献文本中的旧引用编号为新编号（匹配原 swarm 的 _apply_renumber_map）
        _apply_renumber_to_contributions(shared_ctx, renumber_map)

        timeout_occurred = state.get("timeout_occurred", False)
        final_answer = await lead_agent.synthesize_results(
            question=state["question"],
            shared_context=shared_ctx,
            timeout_occurred=timeout_occurred,
        )

        if event_callback:
            lead_agent.set_on_thinking(None)
            lead_agent.set_on_thinking_done(None)

        # 程序化追加参考资料章节
        if swarm_citations:
            ref_section = _format_references_section(swarm_citations)
            if ref_section:
                final_answer += "\n" + ref_section

        # 合并子会话到主会话
        for agent_id, contribs in contributions.items():
            for contrib in contribs:
                sub_session_id = f"{session_id}:{agent_id}:{contrib.get('subtask_id', '')}"
                answer = contrib["result"].get("answer", "")
                coordinator.short_term_memory.merge_sub_session(
                    main_session_id=session_id,
                    sub_session_id=sub_session_id,
                    summary_text=f"[{agent_id}] {answer}" if answer else "",
                    role="assistant",
                )

        # 保存用户问题到主会话
        await coordinator.short_term_memory.add_message(
            session_id=session_id,
            role="user",
            content=state["question"],
        )

        # 免责声明
        disclaimer = PromptLoader.render(
            "validation/swarm_disclaimer.j2",
            timeout_occurred=timeout_occurred,
            completed_agents_count=len(completed_agents),
        )

        return {
            "final_answer": final_answer,
            "citations": swarm_citations,
            "usage": swarm_usage,
            "agents_involved": completed_agents,
            "suggestions": _extract_suggestions(final_answer),
            "disclaimer": disclaimer,
            "timeout_occurred": timeout_occurred,
            "swarm_metadata": {
                "num_subtasks": len(state.get("subtasks", [])),
                "completed_agents": completed_agents,
                "timeout": timeout_occurred,
            },
        }

    async def _finalize(state: SupervisorState) -> dict:
        """节点: 统一收尾（替代 coordinator._finalize）"""
        if state.get("_swarm_finalized"):
            return {}

        session_id = state["session_id"]
        start_time_str = state.get("start_time", "")
        try:
            start_time = datetime.fromisoformat(start_time_str) if start_time_str else datetime.now()
        except (ValueError, TypeError):
            start_time = datetime.now()

        end_time = datetime.now()
        total_time = (end_time - start_time).total_seconds()

        # LTM fire-and-forget
        final_answer = state.get("final_answer", "")
        mode = state.get("mode", "single_agent")
        usage = state.get("usage", {})

        ltm_task = asyncio.ensure_future(coordinator._save_long_term_memory(
            session_id=session_id,
            question=state["question"],
            answer=final_answer,
            metadata={
                "mode": mode,
                "subtasks_count": len(state.get("subtasks", [])),
                "total_time": total_time,
                "total_tokens": usage.get("total_tokens", 0),
            },
        ))

        return {
            "total_time": total_time,
            "_swarm_finalized": True,
        }

    # ===== 条件路由函数 =====

    def _route_by_subtask_count(state: SupervisorState) -> str:
        """根据子任务数量路由"""
        subtasks = state.get("subtasks", [])
        if len(subtasks) == 1:
            return "single"
        elif len(subtasks) >= 2:
            return "swarm"
        else:
            return "fallback"

    def _fan_out_to_workers(state: SupervisorState) -> List[Send]:
        """Send API 扇出（Map 阶段）"""
        subtasks = state.get("subtasks", [])
        session_id = state["session_id"]
        logger.info(f"[SupervisorGraph] Send fan-out: {len(subtasks)} Workers")

        sends = []
        for st in subtasks:
            agent_id = st.get("assigned_agent", "consultation_agent")
            subtask_id = st.get("id", str(uuid.uuid4()))
            sub_session_id = f"{session_id}:{agent_id}:{subtask_id}"

            sends.append(Send(
                node="worker_executor",
                arg={
                    "subtask": st,
                    "agent_id": agent_id,
                    "sub_session_id": sub_session_id,
                    "session_id": session_id,
                    "question": state["question"],
                }
            ))

        return sends

    # ===== 构建图 =====

    builder = StateGraph(SupervisorState)

    # 注册节点
    builder.add_node("retrieve_memories", _retrieve_memories)
    builder.add_node("clarify", _clarify)
    builder.add_node("assess_decompose", _assess_decompose)
    builder.add_node("single_agent", _single_agent_node)
    builder.add_node("send_workers", _send_workers_node)
    builder.add_node("worker_executor", _worker_executor_node)
    builder.add_node("synthesize_results", _synthesize_results)
    builder.add_node("finalize", _finalize)

    # 边连接
    builder.add_edge(START, "retrieve_memories")
    builder.add_edge("retrieve_memories", "clarify")
    builder.add_edge("clarify", "assess_decompose")

    # 条件路由：single / swarm / fallback
    builder.add_conditional_edges(
        "assess_decompose",
        _route_by_subtask_count,
        {
            "single": "single_agent",
            "swarm": "send_workers",
            "fallback": "single_agent",  # fallback 复用 single_agent 节点
        }
    )

    # 单 Agent / Fallback → finalize
    builder.add_edge("single_agent", "finalize")

    # Swarm: Send fan-out
    builder.add_conditional_edges(
        "send_workers",
        _fan_out_to_workers,
        {"worker_executor": "worker_executor"},
    )

    # Worker 完成 → synthesize
    builder.add_edge("worker_executor", "synthesize_results")
    builder.add_edge("synthesize_results", "finalize")
    builder.add_edge("finalize", END)

    # 编译
    return builder.compile(
        checkpointer=MemorySaver(),
    )


# ===== 辅助函数 =====

def _inject_worker_callbacks(agent, agent_id: str, event_callback: Callable):
    """为 Worker Agent 注入流式回调"""
    def _on_thinking(content, iteration):
        event_callback(Event(
            type=EventType.AGENT_THINKING,
            source_agent=agent_id,
            data={"content": content, "iteration": iteration},
        ))

    def _on_tool_step(tool_name, arguments, result, iteration, success):
        event_callback(Event(
            type=EventType.AGENT_TOOL_STEP,
            source_agent=agent_id,
            data={
                "tool_name": tool_name,
                "arguments": {k: str(v)[:100] for k, v in arguments.items()}
                    if isinstance(arguments, dict) else str(arguments)[:100],
                "result": result,
                "iteration": iteration,
                "success": success,
            },
        ))

    def _on_thinking_done(iteration, elapsed_seconds):
        event_callback(Event(
            type=EventType.AGENT_THINKING_DONE,
            source_agent=agent_id,
            data={"iteration": iteration, "elapsed_seconds": elapsed_seconds},
        ))

    def _on_content_token(token):
        event_callback(Event(
            type=EventType.AGENT_CONTENT_DELTA,
            source_agent=agent_id,
            data={"token": token},
        ))

    def _on_questionnaire(questionnaire_id, questionnaire_data):
        event_callback(Event(
            type=EventType.AGENT_QUESTIONNAIRE,
            source_agent=agent_id,
            data={
                "questionnaire_id": questionnaire_id,
                "questionnaire_data": questionnaire_data,
            },
        ))

    if hasattr(agent, 'set_on_thinking'):
        agent.set_on_thinking(_on_thinking)
        agent.set_on_tool_step(_on_tool_step)
        agent.set_on_thinking_done(_on_thinking_done)
        agent.set_on_content_token(_on_content_token)
        agent.set_on_questionnaire(_on_questionnaire)


def _cleanup_worker_callbacks(agent):
    """清理 Worker Agent 的流式回调"""
    if hasattr(agent, 'set_on_thinking'):
        agent.set_on_thinking(None)
        agent.set_on_tool_step(None)
        agent.set_on_thinking_done(None)
        agent.set_on_content_token(None)
        agent.set_on_questionnaire(None)


def _apply_renumber_to_contributions(shared_ctx, renumber_map: Dict[str, Dict[int, int]]):
    """将 SharedContext 中各 Worker 贡献文本中的旧引用编号替换为新编号

    与 SwarmCoordinator._apply_renumber_map 行为一致。
    """
    citation_pattern = re.compile(r'\[(\d+(?:[,\-]\d+)*)\]')

    for agent_id, mapping in renumber_map.items():
        if not mapping:
            continue
        contribs = shared_ctx.agent_contributions.get(agent_id, [])
        for contrib in contribs:
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


def _extract_suggestions(final_answer: str) -> List[str]:
    """从最终答案中提取建议"""
    suggestions = []
    if "## 核心建议" in final_answer or "【核心建议】" in final_answer:
        start_marker = "## 核心建议" if "## 核心建议" in final_answer else "【核心建议】"
        start_idx = final_answer.find(start_marker) + len(start_marker)
        end_match = re.search(r'\n## ', final_answer[start_idx:])
        end_idx = start_idx + end_match.start() if end_match else len(final_answer)
        suggestions_text = final_answer[start_idx:end_idx]
        matches = re.findall(r'\d+\.\s*([^\n]+)', suggestions_text)
        suggestions = matches[:5]
    return suggestions or ["请遵循医嘱，注意休息和营养"]
