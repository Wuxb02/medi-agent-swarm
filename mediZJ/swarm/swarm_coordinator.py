"""
SwarmCoordinator：Swarm 入口和智能路由

注意：这不是编排器！
- 只负责路由决策：简单问题 → 单 Agent，复杂问题 → Swarm
- 不控制 Agent 执行
- 不编排任务顺序

类比：交通信号灯，决定车辆走哪条路，但不控制车辆如何行驶

处理链路：统一走 LangGraph SupervisorGraph + Send API（Map-Reduce）。
"""
import asyncio
import json
import os
import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable
from loguru import logger

from mediZJ.core import LLMClient
from mediZJ.core.prompt_loader import PromptLoader
from mediZJ.memory.prompt_prefix import PromptPrefixAssembler
from mediZJ.swarm.lead_agent import LeadAgent
from mediZJ.swarm.intent_classifier import IntentClassifier
from mediZJ.lgraph.worker import create_worker, Worker
from mediZJ.memory import (
    SessionSummaryManager,
    SessionSummary,
    ShortTermMemory,
    MedicalMemoryContextBuilder,
    PersonalProfile,
)

# Trace 惰性导入
try:
    from mediZJ.trace.context import traced_span
    from mediZJ.trace.models import SpanType, AgentAttributes
    _TRACE = True
except ImportError:
    _TRACE = False

# LangGraph 依赖
try:
    from mediZJ.lgraph.tool_registry import ToolRegistry
    from mediZJ.core.skill_loader import discover_skills
    _LANGGRAPH_AVAILABLE = True
except ImportError as e:
    _LANGGRAPH_AVAILABLE = False
    logger.warning(f"langgraph 模块不可用: {e}")


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
        self.user_id = user_id or "default"

        # 初始化 LeadAgent 与三个 Worker
        self.lead_agent = LeadAgent(
            llm_client=self.llm_client,
            questionnaire_manager=self.questionnaire_manager,
        )
        self._workers: Dict[str, Worker] = {
            "consultation_agent": create_worker("consultation_agent", self.llm_client),
            "diagnostic_agent": create_worker("diagnostic_agent", self.llm_client),
            "research_agent": create_worker("research_agent", self.llm_client),
        }

        # 记忆管理器
        self.session_manager = SessionSummaryManager()
        self.short_term_memory = ShortTermMemory(
            storage_type=os.getenv("WORKING_MEMORY_STORAGE", "redis"),
            llm_client=self.llm_client,
            enable_compression=False,
        )
        self.personal_profile = PersonalProfile(user_id=user_id or "default")
        self.memory_context_builder = MedicalMemoryContextBuilder(
            store=self.personal_profile._structured,
            working_memory=self.short_term_memory,
        )

        # 意图识别（用于医疗流程门控）
        self.intent_classifier = IntentClassifier(llm_client=self.llm_client)

        # LangGraph ToolRegistry
        self._tool_registry = None
        self._init_langgraph_registry()

        # Worker 仅保留工作记忆写入能力，上下文由统一构建器提供。
        for worker in self._workers.values():
            worker.short_term_memory = self.short_term_memory

        logger.info(f"SwarmCoordinator initialized with {len(self._workers)} workers")
        logger.info(
            "Memory system: working={}, structured=sqlite",
            self.short_term_memory.storage_type,
        )

    def get_worker(self, agent_id: str) -> Optional[Worker]:
        """根据 agent_id 返回对应的 Worker 实例"""
        return self._workers.get(agent_id)

    # ===== LangGraph ToolRegistry =====

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

        # 注册基础工具：question_for_user（仅 LeadAgent 可见，Worker 不可调用）
        from mediZJ.core.tools.questionnaire import create_question_for_user_tool

        def _get_manager():
            return self.questionnaire_manager

        q_func = create_question_for_user_tool(_get_manager)
        self._tool_registry.register_base_tool(
            name="question_for_user",
            func=q_func,
            description="向用户发送结构化问卷，收集诊断所需信息。",
            allowed_agents=["lead_agent"],
        )

        logger.info(
            f"[LangGraph] ToolRegistry initialized: "
            f"{len(self._tool_registry)} tools, "
            f"{len(self._tool_registry.get_skill_names())} skills"
        )

    async def process(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """处理用户问题

        Pipeline: 检索 → 澄清 → 分解 → 路由（LangGraph SupervisorGraph）→ 收尾
        """
        if not _LANGGRAPH_AVAILABLE or self._tool_registry is None:
            raise RuntimeError(
                "LangGraph 模块不可用（缺少 langgraph 依赖或 Skill 未发现），无法处理请求"
            )

        start_time = datetime.now()
        if session_id is None:
            session_id = f"{start_time.strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
        trace_id = trace_id or str(uuid.uuid4())
        trace_collector = self._init_trace(trace_id)

        logger.info(f"[LangGraph] Processing (session={session_id}): {question[:300]}{'...' if len(question) > 300 else ''}")

        graph = self.build_graph(event_callback=self.event_callback)
        config = {"configurable": {"thread_id": session_id}}
        initial_state = self.build_initial_state(
            question, context, session_id, start_time, trace_id,
        )

        try:
            result_state = await self.run_graph(graph, initial_state, config)
        except Exception as e:
            logger.error(f"[LangGraph] 图执行异常: {e}")
            return {
                "answer": f"系统处理异常: {e}",
                "session_id": session_id,
                "swarm_enabled": False,
                "agents_involved": [],
                "error": str(e),
            }

        result = self.compose_result(question, result_state, start_time, session_id, trace_id=trace_id)

        await self._flush_trace(
            trace_collector,
            trace_id,
            session_id,
            question,
            result["mode"],
            result,
        )
        return result

    # ===== SupervisorGraph 构建与执行（供 API 层流式路径复用）=====

    def build_graph(self, event_callback: Optional[Callable] = None,
                    hitl_enabled: bool = False):
        """构建 SupervisorGraph（每次调用返回新图，含独立 MemorySaver）

        Args:
            event_callback: 事件回调（流式 SSE 推送）
            hitl_enabled: 是否启用 HITL 问卷（流式路径 True，非流式 False）
        """
        from mediZJ.lgraph.supervisor_graph import build_supervisor_graph
        return build_supervisor_graph(
            self,
            self._tool_registry,
            event_callback,
            hitl_enabled=hitl_enabled,
        )

    def build_initial_state(
        self,
        question: str,
        context: Optional[Dict[str, Any]],
        session_id: str,
        start_time: datetime,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建 SupervisorState 初始状态"""
        return {
            "question": question,
            "session_id": session_id,
            "context": context or {},
            "start_time": start_time.isoformat(),
            "trace_id": trace_id or "",
            "clarify_complete": False,
            "clarify_round": 0,
            "subtasks": [],
            "swarm_contributions": {},
            "swarm_subtasks_status": {},
            "all_references": {},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "_swarm_finalized": False,
        }

    async def run_graph(self, graph, initial_state: Dict[str, Any],
                        config: Dict[str, Any], resume: Any = None) -> Dict[str, Any]:
        """执行 SupervisorGraph；interrupt 挂起时返回 {"_interrupted": True}

        Args:
            resume: 非 None 时用 Command(resume=...) 从 interrupt 断点恢复
        """
        from langgraph.types import Command

        try:
            if resume is not None:
                result = await graph.ainvoke(Command(resume=resume), config)
            else:
                result = await graph.ainvoke(initial_state, config)
        except Exception as e:
            # LangGraph 在 interrupt 时可能抛 GraphInterrupt（取决于版本/stream 模式）
            if getattr(e, "__class__", None) and "GraphInterrupt" in type(e).__name__:
                logger.info(
                    f"[LangGraph] 图在 interrupt 处挂起 "
                    f"(session={config.get('configurable', {}).get('thread_id')})"
                )
                return {"_interrupted": True}
            raise

        # ainvoke 正常返回但状态含 __interrupt__（LangGraph 1.x 行为）→ 同样视为挂起
        if isinstance(result, dict) and "__interrupt__" in result:
            logger.info(
                f"[LangGraph] 图在 interrupt 处挂起 "
                f"(session={config.get('configurable', {}).get('thread_id')})"
            )
            return {"_interrupted": True}

        return result

    def compose_result(self, question: str, result_state: Dict[str, Any],
                       start_time: datetime, session_id: str,
                       trace_id: Optional[str] = None) -> Dict[str, Any]:
        """将 SupervisorState 结果组装为对外 result。"""
        end_time = datetime.now()
        trace_id = trace_id or str(uuid.uuid4())

        result = {
            "answer": result_state.get("final_answer", ""),
            "suggestions": result_state.get("suggestions", []),
            "session_id": session_id,
            "trace_id": trace_id,
            "swarm_enabled": result_state.get("swarm_enabled", False),
            "agents_involved": result_state.get("agents_involved", []),
            "subtasks_completed": len(result_state.get("swarm_contributions", {})),
            "total_time": result_state.get("total_time", (end_time - start_time).total_seconds()),
            "swarm_metadata": result_state.get("swarm_metadata", {}),
            "timeout_occurred": result_state.get("timeout_occurred", False),
            "usage": result_state.get("usage", {}),
            "citations": result_state.get("citations", []),
            "mode": result_state.get("mode", "langgraph"),
            "intent": result_state.get("intent"),
            "skip_long_term_retrieval": result_state.get("skip_long_term_retrieval", False),
            "_swarm_finalized": True,
            "applied_experience_ids": (result_state.get("context") or {}).get(
                "applied_experience_ids", []
            ),
            "experience_assignments": (result_state.get("context") or {}).get(
                "experience_assignments", []
            ),
        }

        logger.info(
            f"[LangGraph] 处理完成: mode={result['mode']}, "
            f"agents={result['agents_involved']}, "
            f"time={result['total_time']:.1f}s"
        )
        return result

    # ===== Trace 追踪 =====

    def _init_trace(self, trace_id: str):
        """惰性初始化 Trace 收集器"""
        try:
            from mediZJ.trace.collector import TraceCollector
            from mediZJ.trace.storage import TraceSqliteStorage
            from mediZJ.trace.context import _current_trace_id

            collector = TraceCollector()
            collector.begin_trace(trace_id)
            if not hasattr(collector, "_storage_set") or not collector._storage_set:
                collector.set_storage(TraceSqliteStorage())
                collector._storage_set = True
            _current_trace_id.set(trace_id)
            logger.debug(f"[Trace] Started for trace={trace_id[:12]}...")
            return collector
        except ImportError:
            return None

    async def _flush_trace(
        self, collector, trace_id, session_id, question, mode, result
    ):
        """持久化 Trace 数据"""
        if collector is None:
            return
        try:
            from mediZJ.trace.models import TraceAttributes
            agents = result.get("agents_involved", []) if isinstance(result, dict) else []
            usage = result.get("usage", {}) if isinstance(result, dict) else {}
            tokens = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            q = question[:200] if question else ""

            root_spans = collector.get_flat_spans(trace_id)
            for s in (root_spans or []):
                if s.span_type.value == "trace":
                    s.trace_attrs = TraceAttributes(
                        session_id=session_id,
                        user_id=getattr(self, "user_id", "default") or "default",
                        mode=mode or "",
                        question_summary=q,
                        agents_involved=agents,
                        total_tokens=tokens,
                        applied_experience_ids=result.get(
                            "applied_experience_ids",
                            [],
                        ),
                        experience_assignments=result.get(
                            "experience_assignments",
                            [],
                        ),
                    )
                    break
            await collector.flush(trace_id)
        except Exception as e:
            logger.warning(f"[Trace] Flush failed: {e}")
        finally:
            try:
                from mediZJ.trace.context import _current_trace_id

                _current_trace_id.set(None)
            except ImportError:
                pass

    # ===== 记忆检索（供 SupervisorGraph 节点复用）=====

    def _refresh_worker_profiles(self, verified_experiences: str = ""):
        """兼容旧调用；画像和策略改由 ContextBuilder 组装。"""
        return None

    # ===== 记忆评估与保存 =====

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

        # 3. 异步调用 LLM（60 秒超时）
        try:
            response = await asyncio.wait_for(
                self.llm_client.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": PromptPrefixAssembler.global_prefix(
                                "你是医疗用户记忆候选提取器。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
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

    async def _save_memory_candidates(self, session_id, question, answer, metadata):
        """仅保存经评估的待确认结构化记忆。"""
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

                logger.info(
                    "Structured memory candidates: score={} info={} records={} session={}",
                    score,
                    len(stable_info),
                    len(medical_records),
                    session_id,
                )
            else:
                logger.warning(
                    "记忆评估未完成，本轮不写入长期记忆: session={}",
                    session_id,
                )
                return

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
                "structured=RECORDED"
            )
        except Exception as e:
            logger.error(f"Structured memory save failed (session={session_id}): {e}")

    # ===== 会话摘要 =====

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
            self.personal_profile._structured.save_episodic_summary(
                session_id=session_id,
                user_id=self.user_id,
                summary=f"问题：{question}\n回答：{final_answer}",
            )
        except Exception as e:
            logger.error(f"Failed to save session summary: {e}")

    # ===== 静态工具（供 SupervisorGraph 复用）=====

    @staticmethod
    def format_references_section(citations: list) -> str:
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

    @staticmethod
    def extract_suggestions(final_answer: str) -> List[str]:
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
