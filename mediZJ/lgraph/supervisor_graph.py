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
import time
import re
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
from loguru import logger

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt
from langgraph.checkpoint.memory import MemorySaver

from mediZJ.lgraph.supervisor_state import SupervisorState
from mediZJ.lgraph.agent_subgraph import build_agent_subgraph
from mediZJ.lgraph.tool_registry import ToolRegistry
from mediZJ.swarm.events import Event, EventType
from mediZJ.swarm.intent_classifier import IntentClassifier
from mediZJ.swarm.lead_agent import _QUESTION_TOOL_SCHEMA
from mediZJ.core.prompt_loader import PromptLoader
from mediZJ.memory.context_builder import (
    MedicalMemoryContext,
)
from mediZJ.memory.prompt_prefix import PromptPrefixAssembler, stable_hash


class _TestContextBuilder:
    """为未注入生产依赖的轻量测试协调器提供统一接口。"""

    def __init__(self, working_memory) -> None:
        self.working_memory = working_memory

    async def build(self, **kwargs) -> MedicalMemoryContext:
        global_prefix = PromptPrefixAssembler.global_prefix(
            kwargs["base_system_prompt"]
        )
        recent = []
        if kwargs.get("include_history", True):
            recent = await self.working_memory.get_recent_messages(
                session_id=kwargs["session_id"], limit=None
            )
        return MedicalMemoryContext(
            global_static_prefix=global_prefix,
            user_stable_prefix="",
            recent_messages=recent,
            global_prefix_hash=stable_hash(global_prefix),
            profile_prefix_hash=stable_hash(""),
            session_id=kwargs["session_id"],
            user_id=kwargs["user_id"],
            agent_id=kwargs["agent_id"],
            call_type=kwargs["call_type"],
            query=kwargs["query"],
            collected_info=kwargs.get("collected_info", ""),
            procedural_strategies=kwargs.get("verified_experiences", ""),
        )


def _memory_builder(coordinator) -> Any:
    """获取统一记忆构建器，并兼容轻量测试协调器。"""
    builder = getattr(coordinator, "memory_context_builder", None)
    if builder is None:
        builder = _TestContextBuilder(coordinator.short_term_memory)
        coordinator.memory_context_builder = builder
    return builder


def _lead_system_prompt(coordinator, method: str, template: str) -> str:
    lead_agent = getattr(coordinator, "lead_agent", None)
    prompt_method = getattr(lead_agent, method, None)
    return prompt_method() if callable(prompt_method) else PromptLoader.load(template)

# Trace
try:
    from mediZJ.trace.context import traced_span
    from mediZJ.trace.models import SpanType
    TRACE_AVAILABLE = True
except ImportError:
    TRACE_AVAILABLE = False


async def retrieve_memories_with_intent_gate(
    coordinator,
    session_id: str,
    question: str,
    intent: str = "medical",
    collected_info: str = "",
    verified_experiences: str = "",
) -> Dict[str, Any]:
    """通过统一构建器获取工作、用户和情景记忆。"""
    base_prompt = _lead_system_prompt(
        coordinator, "_get_system_prompt", "swarm/lead_system.j2"
    )
    context = await _memory_builder(coordinator).build(
        session_id=session_id,
        user_id=getattr(coordinator, "user_id", "default"),
        query=question,
        agent_id="lead_agent",
        call_type="lead_assessment" if intent != "others" else "lead_chat",
        base_system_prompt=base_prompt,
        collected_info=collected_info,
        verified_experiences=verified_experiences,
    )
    result = context.for_lead_agent()
    result.update({
        "memory_context": context,
        "similar_memories": context.episodic_memories,
        "skip_long_term_retrieval": intent == "others",
    })
    return result


async def classify_intent(coordinator, question: str) -> Dict[str, Any]:
    """独立意图分类：others（寒暄/致谢/无关）→ 闲聊直答；medical → 澄清流程。"""
    classifier = getattr(coordinator, "intent_classifier", None) or IntentClassifier()
    result = await classifier.classify(question)
    return {
        "intent": result.intent,
        "intent_confidence": result.confidence,
        "intent_source": result.source,
        "intent_reason": result.reason,
        "skip_long_term_retrieval": result.skip_long_term,
        # others 意图：直接聊天回应，跳过任务分解
        "chat_mode": result.skip_long_term,
    }


def route_by_intent(state: Dict[str, Any]) -> str:
    """根据意图路由：others（寒暄/无关话题）→ 闲聊直答；medical → 澄清流程。"""
    if state.get("chat_mode") or state.get("intent") == "others":
        return "chat_reply"
    return "clarify_decide"


def build_supervisor_graph(
    coordinator,                    # SwarmCoordinator 实例（持有所有 Agent、记忆管理器等）
    tool_registry: ToolRegistry,    # 共享的 ToolRegistry
    event_callback: Optional[Callable] = None,
    hitl_enabled: bool = False,
) -> StateGraph:
    """
    构建主 SupervisorGraph

    Args:
        coordinator: SwarmCoordinator 实例
        tool_registry: 工具注册中心
        event_callback: 事件回调（用于流式 SSE 推送）
        hitl_enabled: 是否启用 HITL 问卷（流式路径 True）。True 时挂载 checkpointer
                      以支持 _clarify 节点内的动态 interrupt()；False 时图无 interrupt、
                      不挂载 checkpointer（按需引入 checkpoint）。

    Returns:
        编译后的 CompiledStateGraph
    """
    lead_agent = coordinator.lead_agent
    consultation_worker = coordinator.get_worker("consultation_agent")
    diagnostic_worker = coordinator.get_worker("diagnostic_agent")
    research_worker = coordinator.get_worker("research_agent")

    def _get_worker_by_id(agent_id: str):
        mapping = {
            "consultation_agent": consultation_worker,
            "diagnostic_agent": diagnostic_worker,
            "research_agent": research_worker,
        }
        return mapping.get(agent_id)

    # ===== 节点函数 =====

    async def _intent_classify(state: SupervisorState) -> dict:
        """节点: 独立意图分类（others → 闲聊直答；medical → 澄清流程）"""
        result = await classify_intent(coordinator, state["question"])

        if event_callback:
            event_callback(Event(
                type=EventType.INTENT_CLASSIFIED,
                source_agent="intent_classifier",
                data={
                    "intent": result["intent"],
                    "confidence": result["intent_confidence"],
                    "source": result["intent_source"],
                    "reason": result.get("intent_reason", ""),
                    "skip_long_term_retrieval": result["skip_long_term_retrieval"],
                },
            ))

        logger.info(f"[SupervisorGraph] 意图识别: {result['intent']} ({result['intent_source']})")
        return result

    async def _retrieve_memories(state: SupervisorState) -> dict:
        """节点: 检索短期记忆 + 长期记忆（位于 clarify 之后、任务分解之前）"""
        result = await retrieve_memories_with_intent_gate(
            coordinator=coordinator,
            session_id=state["session_id"],
            question=state["question"],
            intent=state.get("intent", "medical"),
            collected_info=state.get("collected_info", ""),
            verified_experiences=state.get("context", {}).get(
                "verified_experiences", ""
            ),
        )
        memory_context = result.get("memory_context")
        context_builder = getattr(coordinator, "memory_context_builder", None)
        context_store = getattr(context_builder, "store", None)
        if memory_context is not None and context_store is not None:
            await memory_context.record_usage(
                context_store,
                state.get("trace_id", ""),
            )

        logger.info(
            f"[SupervisorGraph] 记忆检索完成: "
            f"recent={len(result['recent_history'])}条, "
            f"similar={len(result['similar_memories'])}条"
        )

        return result

    # clarify 最大轮数（LLM 自决 + 硬上限）
    _CLARIFY_MAX_ROUNDS = 3

    async def _clarify_decide(state: SupervisorState) -> dict:
        """节点: 澄清决策——LLM 判断是否需要（继续）发问卷

        每轮用已收集答案（clarify_answers）喂回 LLM；判定需澄清则生成
        新问卷 payload 存 clarify_pending 并 goto clarify_ask 挂起。
        达到硬上限 _CLARIFY_MAX_ROUNDS 时不再调用 LLM，直接汇总完成。

        非流式模式（hitl_enabled=False）无 checkpointer，直接跳过澄清。
        """
        if not coordinator.questionnaire_manager:
            logger.debug("QuestionnaireManager 未配置，跳过澄清阶段")
            return {"clarify_complete": True, "collected_info": ""}

        if not hitl_enabled:
            logger.debug("非流式模式（无 HITL），跳过澄清阶段")
            return {"clarify_complete": True, "collected_info": ""}

        current_round = state.get("clarify_round", 0)
        iteration = current_round + 1

        def _emit_clarify_thinking(content: str, status: str = "running") -> None:
            if event_callback:
                event_callback(Event(
                    type=EventType.AGENT_THINKING,
                    source_agent=lead_agent.agent_id,
                    data={
                        "content": content,
                        "iteration": iteration,
                        "phase": "clarify",
                        "title": f"信息澄清（第 {iteration} 轮）",
                        "status": status,
                    },
                ))

        def _emit_clarify_done(status: str = "completed") -> None:
            if event_callback:
                event_callback(Event(
                    type=EventType.AGENT_THINKING_DONE,
                    source_agent=lead_agent.agent_id,
                    data={
                        "iteration": iteration,
                        "phase": "clarify",
                        "status": status,
                        "elapsed_seconds": round(time.monotonic() - think_start, 1),
                    },
                ))

        # 硬上限：先查再调 LLM，保证第 MAX_ROUNDS+1 次 LLM 不会被调用
        if current_round >= _CLARIFY_MAX_ROUNDS:
            logger.info(f"[SupervisorGraph] clarify 达到最大轮数 {_CLARIFY_MAX_ROUNDS}，结束澄清")
            think_start = time.monotonic()
            _emit_clarify_thinking("已达到信息澄清轮数上限，将使用已收集信息继续分析。", "skipped")
            _emit_clarify_done("skipped")
            return {
                "clarify_complete": True,
                "collected_info": _merge_clarify_info(
                    state.get("clarify_rounds", [])
                ),
            }

        # 构建上下文（含已收集答案，供 LLM 判断是否还需追问）
        from mediZJ.core.prompt_loader import PromptLoader as _PL
        from mediZJ.core.tools.questionnaire import (
            parse_questionnaire,
            _build_questionnaire_data,
        )

        context_text = _build_clarify_context(state)
        # 用"问题文本: 答案"可读格式回传已收集信息（避免 q0/q1 内部 key 无法关联问题）
        collected_text = _format_collected_answers(state.get("clarify_rounds", []))
        if collected_text:
            context_text += f"\n\n## 已收集信息\n{collected_text}"

        clarify_context = await _memory_builder(coordinator).build(
            session_id=state["session_id"],
            user_id=getattr(coordinator, "user_id", "default"),
            query=state["question"],
            agent_id="lead_agent",
            call_type="lead_clarify",
            base_system_prompt=lead_agent._get_clarify_system_prompt(),
            collected_info=collected_text,
            include_history=False,
        )
        messages = clarify_context.prompt_messages(
            question=_PL.render(
                "swarm/lead_clarify_user.j2",
                question=state["question"],
                context=context_text,
            )
        )
        think_start = time.monotonic()
        _emit_clarify_thinking("正在判断当前信息是否足以支持后续医疗分析。")
        try:
            if event_callback:
                response = await lead_agent.llm_client.chat_with_tools_stream(
                    messages=messages,
                    tools=[_QUESTION_TOOL_SCHEMA],
                    tool_choice="auto",
                    temperature=0.3,
                    on_reasoning_token=lambda token: _emit_clarify_thinking(token),
                )
            else:
                response = await lead_agent.llm_client.chat_with_tools(
                    messages=messages,
                    tools=[_QUESTION_TOOL_SCHEMA],
                    tool_choice="auto",
                    temperature=0.3,
                )
        except Exception as e:
            logger.error(f"LeadAgent clarify LLM error: {e}")
            _emit_clarify_thinking("信息澄清判断失败，结束澄清并继续后续分析。", "failed")
            _emit_clarify_done("failed")
            return {"clarify_complete": True, "collected_info": ""}

        if not response.has_tool_calls():
            logger.info("[SupervisorGraph] clarify: 无需额外信息，结束澄清")
            _emit_clarify_thinking("当前信息已足够，无需继续追问。", "completed")
            _emit_clarify_done()
            return {
                "clarify_complete": True,
                "collected_info": _merge_clarify_info(
                    state.get("clarify_rounds", [])
                ),
            }

        tool_call = response.tool_calls[0]
        if tool_call.name != "question_for_user":
            logger.warning(f"LeadAgent clarify: 未预期的工具调用 {tool_call.name}")
            _emit_clarify_thinking("模型返回了非预期工具，已结束澄清。", "failed")
            _emit_clarify_done("failed")
            return {
                "clarify_complete": True,
                "collected_info": _merge_clarify_info(
                    state.get("clarify_rounds", [])
                ),
            }

        questionnaire_xml = tool_call.arguments.get("questionnaire", "")
        try:
            questions = parse_questionnaire(questionnaire_xml)
        except Exception as e:
            logger.error(f"LeadAgent clarify: 问卷解析失败: {e}")
            _emit_clarify_thinking("问卷内容解析失败，已结束澄清。", "failed")
            _emit_clarify_done("failed")
            return {
                "clarify_complete": True,
                "collected_info": _merge_clarify_info(
                    state.get("clarify_rounds", [])
                ),
            }

        questionnaire_id = str(uuid.uuid4())
        questionnaire_data = _build_questionnaire_data(questions)

        logger.info(
            f"[SupervisorGraph] 发送问卷 {questionnaire_id} "
            f"(round {current_round + 1})，共 {len(questions)} 个问题"
        )

        # 发射 AGENT_QUESTIONNAIRE 事件（结构与前端依赖一致）
        if event_callback:
            event_callback(Event(
                type=EventType.AGENT_TOOL_STEP,
                source_agent=lead_agent.agent_id,
                data={
                    "tool_name": "question_for_user",
                    "arguments": {
                        "round": iteration,
                        "question_count": len(questions),
                        "question_titles": [question.header for question in questions],
                    },
                    "result": "等待用户回答",
                    "success": True,
                    "iteration": iteration,
                    "phase": "clarify",
                    "status": "waiting",
                },
            ))
            event_callback(Event(
                type=EventType.AGENT_QUESTIONNAIRE,
                source_agent=lead_agent.agent_id,
                data={
                    "questionnaire_id": questionnaire_id,
                    "questionnaire_data": questionnaire_data,
                },
            ))
        _emit_clarify_thinking(f"需要补充 {len(questions)} 项信息，已发起问卷。", "waiting")
        _emit_clarify_done("waiting")

        # 存 payload 到 state，由 clarify_ask 节点 interrupt 挂起
        return {
            "clarify_pending": {
                "type": "questionnaire",
                "questionnaire_id": questionnaire_id,
                "questionnaire_data": questionnaire_data,
                "source_agent": lead_agent.agent_id,
                "_questions_ref": questions,
            },
            "clarify_complete": False,
        }

    async def _clarify_ask(state: SupervisorState) -> dict:
        """节点: 澄清挂起点——唯一的 interrupt()；resume 返回用户 answers

        resume 时该节点重跑（无 LLM/无事件发射），interrupt() 直接返回本轮答案。
        """
        pending = state.get("clarify_pending", {})
        if not pending:
            logger.warning("clarify_ask 被调用但无 pending 问卷，直接结束")
            return {"clarify_complete": True, "collected_info": ""}

        user_answer = interrupt(pending)

        answers = user_answer or {}
        current_round = state.get("clarify_round", 0)
        round_no = current_round + 1

        # 日志用问题文本格式化（避免展示 q0/q1 内部 key）
        from mediZJ.core.tools.questionnaire import format_answers_for_llm
        questions_ref = pending.get("_questions_ref", [])
        readable = format_answers_for_llm(questions_ref, answers) or "（空回答）"
        logger.info(f"[SupervisorGraph] 收到第 {round_no} 轮问卷回答: {readable}")

        if event_callback:
            event_callback(Event(
                type=EventType.AGENT_TOOL_STEP,
                source_agent=lead_agent.agent_id,
                data={
                    "tool_name": "question_for_user",
                    "arguments": {"round": round_no},
                    "result": readable,
                    "success": True,
                    "iteration": round_no,
                    "phase": "clarify",
                    "status": "completed",
                },
            ))

        # 累积答案：本轮 answers 合并进 clarify_answers；记录到 clarify_rounds
        merged = dict(state.get("clarify_answers", {}))
        merged.update(answers)

        prev_rounds = state.get("clarify_rounds", []) or []
        return {
            "clarify_round": round_no,
            "clarify_answers": merged,
            "clarify_rounds": prev_rounds + [{
                "round": round_no,
                "payload": pending,
                "answers": answers,
            }],
            "clarify_pending": None,
        }

    async def _assess_decompose(state: SupervisorState) -> dict:
        """节点: 任务分解（替代 coordinator._do_assess_decompose）"""
        # 注入 LeadAgent thinking 回调
        if event_callback:
            def _on_think(content, iteration):
                event_callback(Event(
                    type=EventType.AGENT_THINKING,
                    source_agent="lead_agent",
                    data={
                        "content": content,
                        "iteration": iteration,
                        "phase": "decompose",
                        "title": "任务分解",
                        "status": "running",
                    },
                ))

            def _on_think_done(iteration, elapsed_seconds):
                event_callback(Event(
                    type=EventType.AGENT_THINKING_DONE,
                    source_agent="lead_agent",
                    data={"iteration": iteration, "elapsed_seconds": elapsed_seconds,
                          "phase": "decompose", "status": "completed"},
                ))

            lead_agent.set_on_thinking(_on_think)
            lead_agent.set_on_thinking_done(_on_think_done)

        enhanced_context = {
            "personal_profile": state.get("personal_profile", ""),
            "recent_history": state.get("recent_history", []),
            "historical_cases": state.get("similar_memories", []),
            "collected_info": state.get("collected_info", ""),
            "verified_experiences": state.get("context", {}).get(
                "verified_experiences", ""
            ),
            "memory_context": state.get("memory_context"),
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
        worker = _get_worker_by_id(agent_id)
        if worker is None:
            worker = consultation_worker
            agent_id = worker.agent_id

        sub_session_id = f"{state['session_id']}:{agent_id}:{task.get('id', 'single')}"

        # 注入流式回调
        if event_callback:
            _inject_worker_callbacks(
                worker,
                agent_id,
                event_callback,
                # 最终内容需先通过统一医疗安全校验。
                stream_final_content=False,
            )

        # Trace: AGENT span
        _ctx = traced_span(SpanType.AGENT, name=agent_id) if TRACE_AVAILABLE else None
        if _ctx:
            _ctx.__enter__()

        try:
            worker_memory_context = await _memory_builder(coordinator).build(
                session_id=state["session_id"],
                user_id=getattr(coordinator, "user_id", "default"),
                query=state["question"],
                agent_id=agent_id,
                call_type=agent_id,
                base_system_prompt=worker.get_base_system_prompt_stable(),
                collected_info=state.get("collected_info", ""),
                verified_experiences=state.get("context", {}).get(
                    "verified_experiences", ""
                ),
            )
            # 构建并执行 AgentSubGraph
            subgraph = build_agent_subgraph(
                worker=worker,
                tool_registry=tool_registry,
                max_iterations=worker.config.get('max_iterations', 10),
                max_tool_calls=2,
                on_thinking=worker.on_thinking,
                on_tool_step=worker.on_tool_step,
                on_thinking_done=worker.on_thinking_done,
                on_content_token=worker.on_content_token,
            )

            result = await subgraph.ainvoke({
                "agent_id": agent_id,
                "sub_session_id": sub_session_id,
                "session_id": state["session_id"],
                "subtask_id": task.get("id", ""),
                "subtask_type": task.get("type", ""),
                "subtask_description": task.get("description", ""),
                "question": state["question"],  # 含图片分析文本的完整问题
                "memory_context": worker_memory_context,
                "max_iterations": worker.config.get('max_iterations', 10),
                "max_tool_calls": 2,
            })
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)
            if event_callback:
                _cleanup_worker_callbacks(worker)

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
            ref_section = coordinator.format_references_section(citations)
            if ref_section:
                reference_text = "\n" + ref_section
                final_answer += reference_text

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
            "suggestions": coordinator.extract_suggestions(final_answer),
        }

    async def _chat_reply_node(state: SupervisorState) -> dict:
        """节点: 闲聊模式——others 意图时 LeadAgent 直接聊天回应，跳过任务分解"""
        memory_context = await _memory_builder(coordinator).build(
            session_id=state["session_id"],
            user_id=getattr(coordinator, "user_id", "default"),
            query=state["question"],
            agent_id="lead_agent",
            call_type="lead_chat",
            base_system_prompt=PromptLoader.load("swarm/chat_reply.j2"),
        )
        enhanced_context = memory_context.for_lead_agent()

        _ctx = traced_span(SpanType.STAGE, name="chat_reply") if TRACE_AVAILABLE else None
        if _ctx:
            _ctx.__enter__()

        try:
            reply = await lead_agent.chat_reply(
                question=state["question"],
                context=enhanced_context,
                event_callback=event_callback,
            )
        finally:
            if _ctx:
                _ctx.__exit__(None, None, None)

        answer = reply.get("answer", "")

        # 记录到短期记忆（chat 模式也需要，否则后续"我刚才问了什么"无法召回）
        await coordinator.short_term_memory.add_message(
            session_id=state["session_id"], role="user",
            content=state["question"],
        )
        await coordinator.short_term_memory.add_message(
            session_id=state["session_id"], role="assistant",
            content=answer,
        )

        logger.info(f"[SupervisorGraph] 闲聊回复完成: {answer[:100]}")

        return {
            "final_answer": answer,
            "citations": [],
            "usage": {},
            "agents_involved": ["lead_agent"],
            "swarm_enabled": False,
            "mode": "chat",
            "route_reason": "意图为 others，LeadAgent 直接聊天回应",
            "suggestions": [],
            "subtasks": [],
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
        worker = _get_worker_by_id(agent_id)
        if worker is None:
            worker = consultation_worker
            agent_id = worker.agent_id

        subtask = state.get("subtask", {})
        sub_session_id = state.get("sub_session_id", "")

        # 注入流式回调
        if event_callback:
            _inject_worker_callbacks(worker, agent_id, event_callback)

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
            worker_memory_context = await _memory_builder(coordinator).build(
                session_id=state.get("session_id", ""),
                user_id=getattr(coordinator, "user_id", "default"),
                query=state["question"],
                agent_id=agent_id,
                call_type=agent_id,
                base_system_prompt=worker.get_base_system_prompt_stable(),
            )
            subgraph = build_agent_subgraph(
                worker=worker,
                tool_registry=tool_registry,
                max_iterations=worker.config.get('max_iterations', 10),
                max_tool_calls=2,
                on_thinking=worker.on_thinking,
                on_tool_step=worker.on_tool_step,
                on_thinking_done=worker.on_thinking_done,
                on_content_token=worker.on_content_token,
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
                    "memory_context": worker_memory_context,
                    "max_iterations": worker.config.get('max_iterations', 10),
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
                _cleanup_worker_callbacks(worker)

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
                    data={
                        "content": content,
                        "iteration": iteration,
                        "phase": "synthesize",
                        "title": "结果汇总",
                        "status": "running",
                    },
                ))
            def _on_think_done_synth(iteration, elapsed_seconds):
                event_callback(Event(
                    type=EventType.AGENT_THINKING_DONE,
                    source_agent="lead_agent",
                    data={
                        "iteration": iteration,
                        "elapsed_seconds": elapsed_seconds,
                        "phase": "synthesize",
                        "status": "completed",
                    },
                ))
            lead_agent.set_on_thinking(_on_think_synth)
            lead_agent.set_on_thinking_done(_on_think_done_synth)

        # 收集所有贡献的 answer 文本 + 引用统一（匹配原 swarm 的 _unify_swarm_references + _apply_renumber_map）
        completed_agents = []
        swarm_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_prompt_tokens": 0,
        }
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
                swarm_usage["cached_prompt_tokens"] += u.get(
                    "cached_prompt_tokens", 0
                )
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
        swarm_usage["cache_hit_ratio"] = (
            swarm_usage["cached_prompt_tokens"] / swarm_usage["prompt_tokens"]
            if swarm_usage["prompt_tokens"]
            else None
        )
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
        synthesis_context = await _memory_builder(coordinator).build(
            session_id=session_id,
            user_id=getattr(coordinator, "user_id", "default"),
            query=state["question"],
            agent_id="lead_agent",
            call_type="lead_synthesis",
            base_system_prompt="你是医疗多智能体结果综合器。",
            collected_info=state.get("collected_info", ""),
            verified_experiences=state.get("context", {}).get(
                "verified_experiences", ""
            ),
        )
        final_answer = await lead_agent.synthesize_results(
            question=state["question"],
            shared_context=shared_ctx,
            timeout_occurred=timeout_occurred,
            # 未校验的综合答案不对外流式发送。
            event_callback=None,
            memory_context=synthesis_context,
        )

        if event_callback:
            lead_agent.set_on_thinking(None)
            lead_agent.set_on_thinking_done(None)

        # 程序化追加参考资料章节
        if swarm_citations:
            ref_section = coordinator.format_references_section(swarm_citations)
            if ref_section:
                reference_text = "\n" + ref_section
                final_answer += reference_text

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

        return {
            "final_answer": final_answer,
            "citations": swarm_citations,
            "usage": swarm_usage,
            "agents_involved": completed_agents,
            "suggestions": coordinator.extract_suggestions(final_answer),
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

        start_time_str = state.get("start_time", "")
        try:
            start_time = datetime.fromisoformat(start_time_str) if start_time_str else datetime.now()
        except (ValueError, TypeError):
            start_time = datetime.now()

        end_time = datetime.now()
        total_time = (end_time - start_time).total_seconds()

        return {
            "total_time": total_time,
            "_swarm_finalized": True,
        }

    # ===== 条件路由函数 =====

    def _route_clarify(state: SupervisorState) -> str:
        """澄清路由：有 pending 问卷 → clarify_ask 挂起；否则 → 检索记忆"""
        if state.get("clarify_pending"):
            return "clarify_ask"
        return "retrieve_memories"

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
                    "memory_context": state.get("memory_context"),
                }
            ))

        return sends

    # ===== 构建图 =====

    builder = StateGraph(SupervisorState)

    # 注册节点
    builder.add_node("intent_classify", _intent_classify)
    builder.add_node("chat_reply", _chat_reply_node)
    builder.add_node("clarify_decide", _clarify_decide)
    builder.add_node("clarify_ask", _clarify_ask)
    builder.add_node("retrieve_memories", _retrieve_memories)
    builder.add_node("assess_decompose", _assess_decompose)
    builder.add_node("single_agent", _single_agent_node)
    builder.add_node("send_workers", _send_workers_node)
    builder.add_node("worker_executor", _worker_executor_node)
    builder.add_node("synthesize_results", _synthesize_results)
    builder.add_node("finalize", _finalize)

    # 边连接
    builder.add_edge(START, "intent_classify")

    # 意图路由：others（寒暄/无关话题）→ 闲聊直答；医疗 → 澄清 → 检索 → 分解
    builder.add_conditional_edges(
        "intent_classify",
        route_by_intent,
        {
            "chat_reply": "chat_reply",
            "clarify_decide": "clarify_decide",
        }
    )

    # clarify 多轮循环：decide → (有问卷) ask → decide；无问卷 → 检索记忆
    builder.add_conditional_edges(
        "clarify_decide",
        _route_clarify,
        {
            "clarify_ask": "clarify_ask",
            "retrieve_memories": "retrieve_memories",
        }
    )
    builder.add_edge("clarify_ask", "clarify_decide")

    builder.add_edge("chat_reply", "finalize")

    # 先澄清完成 → 再检索记忆 → 最后任务分解
    builder.add_edge("retrieve_memories", "assess_decompose")

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

    # 编译：仅当启用 HITL（流式问卷）时才引入 checkpointer —— 按需引入 checkpoint
    if hitl_enabled:
        return builder.compile(
            checkpointer=MemorySaver(),
        )
    return builder.compile()


# ===== 辅助函数 =====

def _build_clarify_context(state: SupervisorState) -> str:
    """构建 clarify 决策阶段的上下文文本（个人档案/近期历史/历史案例）"""
    parts = []
    if state.get("personal_profile"):
        parts.append(f"## 用户档案\n{state['personal_profile']}")
    if state.get("recent_history"):
        parts.append(f"近期对话: {len(state['recent_history'])} 条消息")
    if state.get("similar_memories"):
        parts.append(f"历史相似案例: {len(state['similar_memories'])} 个")
    return "\n\n".join(parts) if parts else "无"


def _format_collected_answers(rounds: List[Dict[str, Any]]) -> str:
    """将已收集的问卷答案格式化为 LLM 可读文本（"问题文本: 答案"）

    用每轮 payload 中保存的 _questions_ref（Question 对象，含 header）还原
    问题文本，避免以 q0/q1 等内部 key 输出导致 LLM 无法关联问题而重复提问。
    """
    from mediZJ.core.tools.questionnaire import format_answers_for_llm

    if not rounds:
        return ""
    parts = []
    for r in rounds:
        questions = (r.get("payload") or {}).get("_questions_ref", [])
        answers = r.get("answers", {})
        formatted = format_answers_for_llm(questions, answers)
        if formatted:
            parts.append(formatted)
    return "\n".join(parts)


def _merge_clarify_info(rounds: List[Dict[str, Any]]) -> str:
    """汇总所有澄清轮次答案为 collected_info 文本（供 assess_decompose 注入）"""
    from mediZJ.core.tools.questionnaire import format_answers_for_llm

    parts = []
    for r in rounds:
        questions = (r.get("payload") or {}).get("_questions_ref", [])
        answers = r.get("answers", {})
        formatted = format_answers_for_llm(questions, answers)
        if formatted:
            parts.append(formatted)
    return "\n".join(parts)


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


def _inject_worker_callbacks(
    worker,
    agent_id: str,
    event_callback: Callable,
    stream_final_content: bool = False,
):
    """为 Worker 注入流式回调"""
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
            data={"token": token, "is_final": True},
        ))

    worker.set_on_thinking(_on_thinking)
    worker.set_on_tool_step(_on_tool_step)
    worker.set_on_thinking_done(_on_thinking_done)
    worker.set_on_content_token(
        _on_content_token if stream_final_content else None
    )


def _cleanup_worker_callbacks(worker):
    """清理 Worker 的流式回调"""
    worker.set_on_thinking(None)
    worker.set_on_tool_step(None)
    worker.set_on_thinking_done(None)
    worker.set_on_content_token(None)
