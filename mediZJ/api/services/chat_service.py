"""问答服务：封装 process_with_swarm + 流式事件推送"""
import asyncio
import inspect
import json
import os
import uuid
from typing import Dict, Any, Optional, AsyncGenerator, List
from datetime import datetime
from loguru import logger

from starlette.requests import Request as StarletteRequest

from mediZJ.swarm.swarm_coordinator import SwarmCoordinator
from mediZJ.swarm.events import Event
from mediZJ.api.models.chat import ChatRequest, ChatResponse, Citation
from mediZJ.api.services.session_runtime import SessionRuntime
from mediZJ.core.questionnaire_manager import QuestionnaireManager
from mediZJ.memory.session_summary import DEFAULT_SESSION_SUMMARY_DIR
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.session_vector_store import SessionVectorStore
from mediZJ.validation.medical_answer import MedicalAnswerVerifier

# 事件持久化目录（与 SessionSummaryManager 一致）
_SUMMARY_DIR = DEFAULT_SESSION_SUMMARY_DIR

_session_db = SessionDB()
_session_vectors: Optional[SessionVectorStore] = None
_answer_verifier: Optional[MedicalAnswerVerifier] = None


def _get_answer_verifier() -> MedicalAnswerVerifier:
    """按需构建最终回答校验器。"""
    global _answer_verifier
    if _answer_verifier is None:
        from mediZJ.core.llm_client import LLMClient

        semantic_enabled = os.getenv(
            "MEDICAL_SEMANTIC_VERIFY_ENABLED", "true"
        ).lower() in {"1", "true", "yes"}
        _answer_verifier = MedicalAnswerVerifier(
            llm_client=LLMClient() if semantic_enabled else None
        )
    return _answer_verifier


async def _verify_final_result(
    question: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """在持久化和输出前执行唯一的安全门。"""
    answer, verification = await _get_answer_verifier().verify_and_rewrite(
        question,
        result.get("answer", ""),
        result.get("citations", []),
    )
    result["answer"] = answer
    result["citations"] = verification.validated_citations
    result["verification"] = verification.to_dict()
    return result


def _get_session_vectors() -> SessionVectorStore:
    """按需初始化会话向量库，避免 API 导入时加载模型。"""

    global _session_vectors
    if _session_vectors is None:
        _session_vectors = SessionVectorStore()
    return _session_vectors

# Trace 惰性导入
try:
    from mediZJ.trace.collector import TraceCollector
    _TRACE_AVAIL = True
except ImportError:
    _TRACE_AVAIL = False

# 问卷管理器注册表（session_id → QuestionnaireManager）
# 注：_managers/_manager_last_activity 的读写均发生在单一事件循环的同步代码段内
# （无 await 间隙），get_manager 的 check-then-act 在当前部署模型下是安全的；
# 若未来引入多 worker/多线程，需要改为加锁或迁移到外部存储。
_managers: Dict[str, QuestionnaireManager] = {}
_manager_last_activity: Dict[str, float] = {}

_TTL_SECONDS = 600  # 10 分钟未活动自动清理
_MAX_MANAGERS = 1000  # 最大条目数，超出 LRU 淘汰

# 单次问答请求总超时（秒），覆盖 process() 全流程。
# 默认 300s：高并发时请求会在 LLM 信号量上排队，
# 50 并发 × 16 路 LLM 上限的场景下 p99 延迟可达 200s 量级
_REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "300"))

# per-session 请求互斥锁：同会话并发请求排队执行，
# 防止短期记忆/turn_index/问卷管理器的写竞争
_session_locks: Dict[str, asyncio.Lock] = {}
_session_owners: Dict[str, str] = {}


def _process_kwargs(
    coordinator: SwarmCoordinator,
    question: str,
    context: Dict[str, Any],
    session_id: str,
    trace_id: str,
) -> Dict[str, Any]:
    """构建处理参数，兼容旧扩展实现与测试替身。"""
    kwargs = {
        "question": question,
        "context": context,
        "session_id": session_id,
    }
    if "trace_id" in inspect.signature(coordinator.process).parameters:
        kwargs["trace_id"] = trace_id
    return kwargs


def _get_session_lock(session_id: str) -> asyncio.Lock:
    """获取（或创建）指定会话的请求互斥锁"""
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


async def _restore_short_term(session_id: str, user_id: str) -> None:
    """续聊时从 SQLite 恢复全部历史到短期记忆（幂等，空才回填）

    服务重启或 TTL 过期后短期记忆清空，LeadAgent 拿不到历史上下文；
    从权威源 SQLite 重建完整消息历史，供 _retrieve_memories 节点消费。
    """
    from mediZJ.memory.short_term import ShortTermMemory
    messages = await asyncio.to_thread(
        _session_db.get_recent_turns, session_id, user_id or "default", None
    )
    if messages:
        await ShortTermMemory().restore_session(session_id, messages)


def _cleanup_one(session_id: str):
    """清理单个 session 的管理器"""
    _manager_last_activity.pop(session_id, None)
    mgr = _managers.pop(session_id, None)
    if mgr:
        mgr.cancel_all()
    # 锁未被持有时一并回收，避免 _session_locks 无限增长
    lock = _session_locks.get(session_id)
    if lock is not None and not lock.locked():
        _session_locks.pop(session_id, None)
    _session_owners.pop(session_id, None)


def claim_session(session_id: str, user_id: str) -> None:
    """声明会话归属，阻止不同用户复用同一会话 ID。"""

    active_owner = _session_owners.get(session_id)
    if active_owner is not None and active_owner != user_id:
        raise PermissionError("会话不属于当前用户")

    persisted = _session_db.get_session(session_id)
    if persisted is not None and persisted.get("user_id") != user_id:
        raise PermissionError("会话不属于当前用户")
    _session_owners[session_id] = user_id


def session_owner(session_id: str) -> Optional[str]:
    """返回进行中或已持久化会话的用户 ID。"""

    owner = _session_owners.get(session_id)
    if owner is not None:
        return owner
    persisted = _session_db.get_session(session_id)
    return persisted.get("user_id") if persisted else None


def _cleanup_expired(now: float):
    """删除所有超过 TTL 未活动的条目"""
    expired = [
        sid for sid, ts in _manager_last_activity.items()
        if now - ts > _TTL_SECONDS
    ]
    for sid in expired:
        logger.debug(f"QuestionnaireManager TTL expired: {sid}")
        _cleanup_one(sid)


def get_manager(session_id: str) -> QuestionnaireManager:
    """获取或创建会话的问卷管理器（自动 TTL 清理 + LRU 淘汰）"""
    import time
    now = time.time()

    # 1. TTL 过期清理
    _cleanup_expired(now)

    # 2. 已存在则更新活动时间并返回
    if session_id in _managers:
        _manager_last_activity[session_id] = now
        return _managers[session_id]

    # 3. 数量上限控制：LRU 淘汰最久未活动的条目
    if len(_managers) >= _MAX_MANAGERS:
        lru_sid = min(_manager_last_activity, key=_manager_last_activity.get)
        logger.warning(f"QuestionnaireManager LRU eviction: {lru_sid}")
        _cleanup_one(lru_sid)

    # 4. 创建新管理器
    _managers[session_id] = QuestionnaireManager()
    _manager_last_activity[session_id] = now
    return _managers[session_id]


def remove_manager(session_id: str):
    """清理会话的问卷管理器"""
    _cleanup_one(session_id)


class EventBridge:
    """事件桥接器：拦截 SharedContext 的事件，推送到流"""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: Event):
        await self.queue.put(event)


async def chat_non_stream(request: ChatRequest) -> ChatResponse:
    """非流式问答（同会话请求排队执行）"""
    from fastapi import HTTPException

    session_id = request.session_id or (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    )
    try:
        claim_session(session_id, request.user_id or "default")
        async with _get_session_lock(session_id):
            return await _chat_non_stream_locked(request, session_id)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"请求处理超时（{_REQUEST_TIMEOUT:.0f}s），请稍后重试或简化问题",
        ) from None


async def _chat_non_stream_locked(request: ChatRequest, session_id: str) -> ChatResponse:
    """非流式问答（持锁后的实际处理）"""
    manager = get_manager(session_id)
    trace_id = str(uuid.uuid4())
    from mediZJ.evolution import EvolutionService

    evolution_context = await asyncio.to_thread(
        EvolutionService().get_runtime_context,
        request.user_id or "default",
        request.question,
    )
    runtime_context = {**(request.context or {}), **evolution_context}

    # 续聊恢复：从 SQLite 回填全部历史到短期记忆（幂等，空才回填）
    await _restore_short_term(session_id, request.user_id or "default")

    # 图片分析：先用 Vision 模型将图片转为文字描述，再注入上下文
    question = request.question
    if request.images:
        from mediZJ.api.services.image_analyzer import ImageAnalyzer
        question = await ImageAnalyzer().analyze(request.images, request.question)

    coordinator = SwarmCoordinator(
        questionnaire_manager=manager,
        user_id=request.user_id
    )
    result = await asyncio.wait_for(
        coordinator.process(**_process_kwargs(
            coordinator,
            question,
            runtime_context,
            session_id,
            trace_id,
        )),
        timeout=_REQUEST_TIMEOUT,
    )
    result = await _verify_final_result(question, result)
    # 仅从已通过医疗安全校验的最终回答提取记忆候选。
    memory_saver = getattr(coordinator, "_save_memory_candidates", None)
    if result.get("intent") != "others" and callable(memory_saver):
        try:
            await asyncio.wait_for(
                memory_saver(
                    session_id,
                    question,
                    result.get("answer", ""),
                    {
                        "mode": result.get("mode", "langgraph"),
                        "total_tokens": result.get("usage", {}).get(
                            "total_tokens", 0
                        ),
                    },
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Memory save timeout in non-stream for session={session_id}")
        except Exception as e:
            logger.error(f"Memory save error in non-stream: {e}")

    # 持久化到 SQLite + Milvus（同步驱动，下线程避免阻塞事件循环）
    persist_session_id = result.get("session_id", session_id)
    persisted = {}
    if persist_session_id:
        try:
            persisted = await asyncio.to_thread(
                _persist_session_turn, persist_session_id, request, result, []
            ) or {}
        except Exception as e:
            logger.warning(f"Failed to persist non-stream session turn: {e}")

    # 清理问卷管理器
    remove_manager(session_id)

    return ChatResponse(
        answer=result.get("answer", ""),
        suggestions=result.get("suggestions", []),
        session_id=result.get("session_id", ""),
        assistant_message_id=persisted.get("assistant_message_id", ""),
        trace_id=result.get("trace_id", trace_id),
        swarm_enabled=result.get("swarm_enabled", False),
        agents_involved=result.get("agents_involved", []),
        subtasks_completed=result.get("subtasks_completed", 0),
        total_time=result.get("total_time", 0.0),
        swarm_metadata=result.get("swarm_metadata", {}),
        timeout_occurred=result.get("timeout_occurred", False),
        usage=result.get("usage", {}),
        citations=[
            Citation(**c) for c in result.get("citations", [])
        ],
        verification=result.get("verification"),
    )


async def chat_stream(
    chat_req: ChatRequest,
    http_request: StarletteRequest,
) -> AsyncGenerator[str, None]:
    """流式问答（换行分隔 JSON），同会话请求排队执行

    支持客户端断开时自动取消后台处理任务，避免浪费 LLM token。
    """
    session_id = chat_req.session_id or (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    )
    claim_session(session_id, chat_req.user_id or "default")
    async with _get_session_lock(session_id):
        async for chunk in _chat_stream_impl(chat_req, http_request, session_id):
            yield chunk


async def _chat_stream_impl(
    chat_req: ChatRequest,
    http_request: StarletteRequest,
    session_id: str,
) -> AsyncGenerator[str, None]:
    """流式问答（持锁后的实际处理）"""
    bridge = EventBridge()
    trace_id = str(uuid.uuid4())
    from mediZJ.evolution import EvolutionService

    evolution_context = await asyncio.to_thread(
        EvolutionService().get_runtime_context,
        chat_req.user_id or "default",
        chat_req.question,
    )
    runtime_context = {**(chat_req.context or {}), **evolution_context}

    # 1. 发送 start
    start_event = {"session_id": session_id, "trace_id": trace_id}
    yield _json_line("start", start_event)

    # 收集所有事件用于持久化
    collected_events: List[Dict[str, Any]] = [{"event": "start", "data": start_event}]

    # 1.5 创建会话的问卷管理器
    manager = get_manager(session_id)

    # 1.55 续聊恢复：从 SQLite 回填全部历史到短期记忆（幂等，空才回填）
    await _restore_short_term(session_id, chat_req.user_id or "default")

    # 1.6 注册 Trace 实时推送回调（span 完成时入队 EventBridge）
    trace_collector = None
    if _TRACE_AVAIL:
        collector = TraceCollector()

        def _on_span_complete(span):
            from mediZJ.swarm.events import Event, EventType
            bridge.queue.put_nowait(Event(
                type=EventType.TRACE_SPAN,
                source_agent="trace",
                data={"id": span.id, "trace_id": span.trace_id, "parent_id": span.parent_id,
                      "span_type": span.span_type.value,
                      "name": span.name, "start_time": span.timing.start_time.isoformat(),
                      "duration_ms": span.timing.duration_ms, "status": span.status.value}
            ))

        collector.add_span_callback(trace_id, _on_span_complete)

    # 图片分析：先用 Vision 模型将图片转为文字描述，再注入上下文
    question = chat_req.question
    if chat_req.images:
        from mediZJ.api.services.image_analyzer import ImageAnalyzer
        question = await ImageAnalyzer().analyze(chat_req.images, chat_req.question)

    # 2. 创建协调器 + 启动处理
    from mediZJ.api.services.session_runtime import (
        store_runtime, release_runtime, get_answer_queue, clear_answer_queue,
    )

    coordinator = SwarmCoordinator(
        event_callback=lambda event: bridge.queue.put_nowait(event),
        questionnaire_manager=manager,
        user_id=chat_req.user_id
    )
    if _TRACE_AVAIL:
        trace_collector = coordinator._init_trace(trace_id)

    trace_finished = False

    async def _finish_trace(trace_result: Optional[Dict[str, Any]] = None):
        nonlocal trace_finished
        if trace_finished or trace_collector is None:
            return
        trace_finished = True
        payload = trace_result or {}
        await coordinator._flush_trace(
            trace_collector,
            trace_id,
            session_id,
            question,
            payload.get("mode", "error"),
            payload,
        )

    # 流式路径启用 HITL：构建带 checkpointer 的图，供 clarify interrupt 挂起/恢复
    start_time = datetime.now()
    graph = coordinator.build_graph(
        event_callback=lambda event: bridge.queue.put_nowait(event),
        hitl_enabled=True,
    )
    initial_state = coordinator.build_initial_state(
        question, runtime_context, session_id, start_time,
    )
    config = {"configurable": {"thread_id": session_id}}
    store_runtime(SessionRuntime(
        coordinator=coordinator,
        graph=graph,
        config=config,
        initial_state=initial_state,
        session_id=session_id,
    ))

    async def _run_pipeline(resume: Any = None):
        """执行图：正常执行返回结果；clarify interrupt 挂起返回 {"_interrupted": True}。

        超时仅包裹图执行阶段：interrupt 挂起时任务已返回，等待用户答案期间
        无活跃超时（用户不回答则一直等待）。
        """
        return await asyncio.wait_for(
            coordinator.run_graph(graph, initial_state, config, resume=resume),
            timeout=_REQUEST_TIMEOUT,
        )

    process_task = asyncio.create_task(_run_pipeline())

    # 2.5 启动客户端断开检测任务
    async def _wait_for_disconnect():
        """等待客户端断开连接"""
        while True:
            if await http_request.is_disconnected():
                return
            await asyncio.sleep(0.1)

    disconnect_task = asyncio.create_task(_wait_for_disconnect())

    # 3. 持续从队列读取事件并发送（同时监控断开和完成）
    #
    # thinking 批量缓冲：避免每个 reasoning token 都生成一条 SSE 事件，
    # 改为按 agent+iteration 聚合，每隔 THINK_FLUSH_INTERVAL 秒或缓冲区
    # 达到阈值时批量发送，大幅减少事件数量和网络往返。
    import time as _time
    THINK_FLUSH_INTERVAL = 0.08      # 80ms 批量间隔
    THINK_FLUSH_MIN_CHARS = 20       # 累积至少 20 字符再发送（避免首字符延迟感）
    # phase 必须纳入 key 并原样保留，否则 LeadAgent 第 2 轮澄清会被
    # 前端的旧版 iteration=2 兼容逻辑误判为“结果汇总”。
    _think_buf: Dict[str, Dict[str, Any]] = {}
    _last_think_flush = _time.monotonic()

    def _flush_think_buffer(force: bool = False):
        """将缓冲的 thinking token 合并为批量事件并 yield 到外层 generator"""
        nonlocal _last_think_flush
        now = _time.monotonic()
        for key, entry in list(_think_buf.items()):
            text = entry["content"]
            if not text:
                del _think_buf[key]
                continue
            # 非强制刷新时：间隔未到且字符数不足则跳过
            if not force and now - _last_think_flush < THINK_FLUSH_INTERVAL and len(text) < THINK_FLUSH_MIN_CHARS:
                continue
            # 合并为一个批量 thinking 事件
            batch_dict = {
                "source_agent": entry["agent"],
                "data": {
                    "content": text,
                    "iteration": entry["iteration"],
                    **entry["metadata"],
                },
                "timestamp": datetime.now().isoformat(),
            }
            # 使用闭包捕获当前值（非局部变量引用在 generator 中安全）
            _yield_event("agent_thinking", batch_dict)
            del _think_buf[key]
        if force or not _think_buf:
            _last_think_flush = now

    # 收集 _yield_event 闭包捕获的待发送事件列表
    _pending_yields: List[tuple] = []

    def _yield_event(evt_name: str, evt_data: Dict[str, Any]):
        """将事件加入待发送列表（在 _drain_one 中统一 yield）"""
        _pending_yields.append((evt_name, evt_data))

    def _drain_one(event: Event):
        """处理单个事件：映射类型 → 加入待发送列表或缓冲"""
        mapped_type = _map_event_type(event.type.value)

        # 仅透传最终回答 token。Swarm Worker 的中间产物不得进入正文。
        if mapped_type == "agent_content_delta":
            if event.data.get("is_final"):
                _yield_event(mapped_type, event.to_dict())
            return

        # thinking 事件进入批量缓冲
        if mapped_type == "agent_thinking":
            d = event.data
            agent = event.source_agent
            iteration = d.get("iteration", 0)
            phase = d.get("phase", "")
            key = f"{agent}:{phase}:{iteration}"
            if key not in _think_buf:
                _think_buf[key] = {
                    "content": "",
                    "agent": agent,
                    "iteration": iteration,
                    "metadata": {},
                }
            _think_buf[key]["content"] += d.get("content", "")
            _think_buf[key]["metadata"].update({
                field: d[field]
                for field in ("phase", "title", "status")
                if field in d
            })
            # 达到阈值或 thinking_done 后强制刷新
            total_chars = len(_think_buf[key]["content"])
            if total_chars >= THINK_FLUSH_MIN_CHARS or _time.monotonic() - _last_think_flush >= THINK_FLUSH_INTERVAL:
                _flush_think_buffer(force=False)
            return

        # 非 thinking 事件到达时，先刷新 thinking 缓冲（保证时序）
        _flush_think_buffer(force=True)

        if mapped_type == "agent_questionnaire":
            _yield_event("agent_questionnaire", event.to_dict())
            return

        _yield_event(mapped_type, event.to_dict())

    result = None
    disconnected = False
    # 状态机内已发送 error 事件并结束（无需再走正常完成路径）
    errored = False

    def _is_interrupted(task_result: Any) -> bool:
        """判断图执行结果是否为 interrupt 挂起态"""
        return isinstance(task_result, dict) and task_result.get("_interrupted")

    # 统一状态机：phase="run" 消费事件；phase="wait_answer" 等用户问卷答案后 resume。
    # 图可能经历 0/1/多次 interrupt，resume 后正常完成则退出循环进入排空/收尾。
    phase = "run"
    answer_queue = get_answer_queue(session_id)

    while True:
        if disconnect_task.done():
            disconnected = True
            break

        if phase == "run":
            if process_task.done():
                # 图执行结束：interrupt 挂起 → 等待答案；正常完成 → 排空收尾
                try:
                    task_result = process_task.result()
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Chat processing timeout ({_REQUEST_TIMEOUT:.0f}s), session={session_id}"
                    )
                    yield _json_line("error", {
                        "error": f"请求处理超时（{_REQUEST_TIMEOUT:.0f}s），请稍后重试或简化问题"
                    })
                    errored = True
                    break
                except Exception as e:
                    logger.error(f"Chat processing error: {e}")
                    yield _json_line("error", {"error": str(e)})
                    errored = True
                    break

                if _is_interrupted(task_result):
                    phase = "wait_answer"
                    continue
                break

            # 消费图事件 / 等待 process / disconnect
            get_event = asyncio.ensure_future(bridge.queue.get())
            done, _ = await asyncio.wait(
                [get_event, process_task, disconnect_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                get_event.cancel()
                disconnected = True
                break
            if process_task in done:
                get_event.cancel()
                continue  # 回顶部复查 done（interrupt 挂起 / 完成）

            event = get_event.result()
            _drain_one(event)

            if _think_buf and _time.monotonic() - _last_think_flush >= THINK_FLUSH_INTERVAL:
                _flush_think_buffer(force=True)

            for evt_name, evt_data in _pending_yields:
                json_line = _json_line(evt_name, evt_data)
                collected_events.append({"event": evt_name, "data": evt_data})
                yield json_line
            _pending_yields.clear()

        else:  # phase == "wait_answer"
            logger.info(f"Clarify interrupt pending for session={session_id}, awaiting answer")
            answer_task = asyncio.ensure_future(answer_queue.get())
            done, _ = await asyncio.wait(
                [answer_task, disconnect_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                answer_task.cancel()
                disconnected = True
                break

            answers = answer_task.result()
            logger.info(f"Resuming graph with user answer for session={session_id}")

            # 取回同一 runtime 的 graph/config，复用 checkpointer 完成恢复
            from mediZJ.api.services.session_runtime import get_runtime
            runtime = get_runtime(session_id)
            if runtime is None:
                logger.warning(f"SessionRuntime 已清理 (session={session_id})，丢弃答案")
                break

            process_task = asyncio.create_task(_run_pipeline(resume=answers))
            phase = "run"  # 可能再次 interrupt → 再次进入 wait_answer

    clear_answer_queue(session_id)

    # 4. 排空队列中残留的事件（process 完成但队列未空）
    if not disconnected:
        _flush_think_buffer(force=True)  # 先刷新 thinking 缓冲
        while not bridge.queue.empty():
            try:
                event = bridge.queue.get_nowait()
                _drain_one(event)
            except asyncio.QueueEmpty:
                break
        _flush_think_buffer(force=True)  # 再次刷新（可能新事件产生了 thinking）
        # yield 排空阶段的事件
        for evt_name, evt_data in _pending_yields:
            json_line = _json_line(evt_name, evt_data)
            collected_events.append({"event": evt_name, "data": evt_data})
            yield json_line
        _pending_yields.clear()

    if not disconnected:
        disconnect_task.cancel()
        try:
            await disconnect_task
        except asyncio.CancelledError:
            pass

    # 5. 处理断开情况
    if disconnected:
        logger.info(f"Client disconnected for session={session_id}, cancelling processing")
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            logger.info(f"Processing cancelled for session={session_id}")
        except Exception as e:
            logger.error(f"Error after cancel for session={session_id}: {e}")
        clear_answer_queue(session_id)
        release_runtime(session_id)
        remove_manager(session_id)
        await _finish_trace()
        return

    # 5.5 状态机内已发送 error 事件（超时/异常）：清理后返回，不再走正常完成路径
    if errored:
        clear_answer_queue(session_id)
        release_runtime(session_id)
        remove_manager(session_id)
        await _finish_trace()
        return

    # 6. 正常完成：等待 process_task 结果
    try:
        result = process_task.result()
    except asyncio.TimeoutError:
        logger.warning(
            f"Chat processing timeout ({_REQUEST_TIMEOUT:.0f}s), session={session_id}"
        )
        yield _json_line("error", {
            "error": f"请求处理超时（{_REQUEST_TIMEOUT:.0f}s），请稍后重试或简化问题"
        })
        clear_answer_queue(session_id)
        release_runtime(session_id)
        remove_manager(session_id)
        await _finish_trace()
        return
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        yield _json_line("error", {"error": str(e)})
        clear_answer_queue(session_id)
        release_runtime(session_id)
        remove_manager(session_id)
        await _finish_trace()
        return

    # 防御：若 result 仍是 interrupt 挂起态（异常路径），清理后结束
    if isinstance(result, dict) and result.get("_interrupted"):
        logger.warning(f"Interrupt 未恢复即结束，清理 session={session_id}")
        clear_answer_queue(session_id)
        release_runtime(session_id)
        remove_manager(session_id)
        await _finish_trace()
        return

    # 组装对外 result。
    result = coordinator.compose_result(question, result, start_time, session_id, trace_id=trace_id)
    result = await _verify_final_result(question, result)
    await _finish_trace(result)

    memory_task = None
    memory_saver = getattr(coordinator, "_save_memory_candidates", None)
    if result.get("intent") != "others" and callable(memory_saver):
        memory_task = asyncio.create_task(
            memory_saver(
                session_id,
                question,
                result.get("answer", ""),
                {
                    "mode": result.get("mode", "langgraph"),
                    "total_tokens": result.get("usage", {}).get(
                        "total_tokens", 0
                    ),
                },
            )
        )

    # 6. 发送建议
    suggestions = result.get("suggestions", [])
    if suggestions:
        suggestions_payload = {"suggestions": suggestions}
        yield _json_line("suggestions", suggestions_payload)
        collected_events.append({"event": "suggestions", "data": suggestions_payload})

    # 6.5 持久化到 SQLite + Milvus（在 done 之前，确保前端 loadSessions 能查到）
    # 同步驱动，下线程避免阻塞事件循环
    persisted = {}
    try:
        persisted = await asyncio.to_thread(
            _persist_session_turn, session_id, chat_req, result, collected_events
        ) or {}
    except Exception as e:
        logger.warning(f"Failed to persist session turn: {e}")

    # 7. 发送 done（最后一个事件）
    done_data = {
        "session_id": result.get("session_id", session_id),
        "assistant_message_id": persisted.get("assistant_message_id", ""),
        "trace_id": result.get("trace_id", trace_id),
        "total_time": result.get("total_time", 0.0),
        "swarm_metadata": result.get("swarm_metadata", {}),
        "swarm_enabled": result.get("swarm_enabled", False),
        "agents_involved": result.get("agents_involved", []),
        "answer": result.get("answer", ""),
        "usage": result.get("usage", {}),
        "performance_metrics": result.get("performance_metrics", {}),
        "citations": result.get("citations", []),
        "verification": result.get("verification"),
    }
    yield _json_line("done", done_data)
    collected_events.append({"event": "done", "data": done_data})

    # 7.5 等待结构化记忆候选写入（done 已发送）。
    if memory_task is not None and not memory_task.done():
        try:
            await asyncio.wait_for(memory_task, timeout=90.0)
            logger.info(f"Memory save completed for session={session_id}")
        except asyncio.TimeoutError:
            logger.warning(f"Memory save timeout in chat_stream for session={session_id}")
        except Exception as e:
            logger.error(f"Memory save error in chat_stream: {e}")

    # 8. 持久化事件到 JSON 文件（后台不阻塞返回）
    try:
        _save_session_events(session_id, collected_events, result)
    except Exception as e:
        logger.warning(f"Failed to save session events: {e}")

    # 9. 清理问卷管理器 + 会话运行期 + 答案队列
    clear_answer_queue(session_id)
    release_runtime(session_id)
    remove_manager(session_id)

    # 10. 清理 trace 回调（确保不留残留引用；正常流程 flush 已清理）
    if _TRACE_AVAIL:
        collector.remove_span_callback(trace_id, _on_span_complete)


def _merge_thinking_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并流式 thinking token，同时保留实时事件的完整结构。"""
    merged: List[Dict[str, Any]] = []
    buf_agent: Optional[str] = None
    buf_iteration: Optional[int] = None
    buf_phase: Optional[str] = None
    buf_parts: List[str] = []
    buf_envelope: Optional[Dict[str, Any]] = None
    buf_payload: Optional[Dict[str, Any]] = None

    def _flush():
        """将缓冲区中的 token 拼接为一条完整事件"""
        if not buf_parts or buf_envelope is None or buf_payload is None:
            return
        envelope = dict(buf_envelope)
        payload = dict(buf_payload)
        payload["content"] = "".join(buf_parts)
        envelope["data"] = payload
        merged.append({
            "event": "agent_thinking",
            "data": envelope,
        })

    for ev in events:
        if ev.get("event") != "agent_thinking":
            _flush()
            buf_parts.clear()
            buf_agent = buf_iteration = buf_phase = None
            buf_envelope = buf_payload = None
            merged.append(ev)
            continue

        data = ev.get("data", {})
        agent = data.get("source_agent")
        payload = data.get("data", data)
        iteration = payload.get("iteration")
        phase = payload.get("phase")

        if agent == buf_agent and iteration == buf_iteration and phase == buf_phase:
            # 同一轮 thinking，追加 token
            buf_parts.append(payload.get("content", ""))
            buf_payload.update({
                key: value for key, value in payload.items() if key != "content"
            })
        else:
            # 新的一轮 thinking，先 flush 旧的
            _flush()
            buf_agent = agent
            buf_iteration = iteration
            buf_phase = phase
            buf_parts = [payload.get("content", "")]
            buf_envelope = dict(data)
            buf_payload = dict(payload)

    _flush()
    return merged


def _save_session_events(session_id: str, events: List[Dict[str, Any]], result: Dict[str, Any]):
    """将 SSE 事件列表持久化为 JSON 文件，供历史回放使用"""
    _SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _SUMMARY_DIR / f"session_{session_id}.json"
    # 过滤掉流式内容增量事件（历史回放不需要）
    _SKIP_TYPES = {"agent_content_delta"}
    filtered = [e for e in events if e.get("event") not in _SKIP_TYPES]
    # 合并流式 thinking token 为完整文本
    filtered = _merge_thinking_events(filtered)
    payload = {
        "session_id": session_id,
        "events": filtered,
        "suggestions": result.get("suggestions", []),
        "answer": result.get("answer", ""),
        "agents_involved": result.get("agents_involved", []),
        "subtasks_completed": result.get("subtasks_completed", 0),
        "total_time": result.get("total_time", 0.0),
        "swarm_enabled": result.get("swarm_enabled", False),
        "usage": result.get("usage", {}),
        "performance_metrics": result.get("performance_metrics", {}),
        "citations": result.get("citations", []),
        "verification": result.get("verification"),
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"Saved session events: {filepath}")


def _json_line(event_name: str, data: Dict[str, Any]) -> str:
    """单行 JSON，格式：{"event": "xxx", "data": {...}}\n"""
    return json.dumps(
        {"event": event_name, "data": data},
        ensure_ascii=False,
        default=str
    ) + "\n"


def _map_event_type(event_type: str) -> str:
    """映射内部事件类型到流事件名"""
    mapping = {
        "swarm_started": "agent_start",
        "task_decomposed": "task_decomposed",
        "subtask_started": "agent_start",
        "subtask_completed": "agent_complete",
        "context_updated": "agent_tool_result",
        "agent_question": "agent_tool_call",
        "agent_answer": "agent_tool_result",
        "swarm_completed": "agent_complete",
        "trace_span": "trace_span",
    }
    return mapping.get(event_type, event_type)


def _persist_session_turn(
    session_id: str,
    request: ChatRequest,
    result: Dict[str, Any],
    collected_events: List[Dict[str, Any]],
):
    """将本轮对话持久化到 SQLite，并索引到 Milvus"""
    now = datetime.now().isoformat()
    turn_index = _session_db.get_turn_count(session_id)

    # 过滤掉流式内容增量事件（与 _save_session_events 一致）
    _SKIP_TYPES = {"agent_content_delta"}
    filtered_events = [
        e for e in collected_events if e.get("event") not in _SKIP_TYPES
    ]
    filtered_events = _merge_thinking_events(filtered_events)

    saved = _session_db.save_turn(
        session_id=session_id,
        turn_index=turn_index,
        user_msg={
            "role": "user",
            "content": request.question,
            "timestamp": now,
            "images": request.images or [],
        },
        assistant_msg={
            "role": "assistant",
            "content": result.get("answer", ""),
            "timestamp": now,
            "agent_events": filtered_events,
            "suggestions": result.get("suggestions", []),
            "agents_involved": result.get("agents_involved", []),
            "total_time": result.get("total_time", 0.0),
            "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            "subtasks_completed": result.get("subtasks_completed", 0),
            "mode": "swarm" if result.get("swarm_enabled", False) else "single",
            "parallel_efficiency": result.get("performance_metrics", {}).get("parallel_efficiency", 0),
            "information_coverage": result.get("performance_metrics", {}).get("information_coverage", 0),
            "redundancy": result.get("performance_metrics", {}).get("redundancy", 0),
            "citations": result.get("citations", []),
            "trace_id": result.get("trace_id", ""),
        },
        user_id=request.user_id or "default",
    )

    try:
        from mediZJ.evolution import EvolutionService

        evolution = EvolutionService()
        assistant_message_id = int(saved["assistant_message_id"])
        evolution.storage.record_exposures(
            assistant_message_id,
            request.user_id or "default",
            result.get("experience_assignments", []),
        )
        evolution.maybe_enqueue_sample(
            assistant_message_id,
            request.user_id or "default",
        )
    except Exception as exc:
        logger.warning(f"Failed to enqueue evolution evaluation: {exc}")

    # 索引到 Milvus（每次更新摘要，用于语义搜索）
    try:
        session_data = _session_db.get_session(
            session_id,
            request.user_id or "default",
        )
        if session_data:
            messages = session_data.get("messages", [])
            user_msgs = [m for m in messages if m["role"] == "user"]
            first_q = user_msgs[0]["content"][:100] if user_msgs else ""
            turn_count = session_data.get("turn_count", 0)
            summary = (
                f"会话共 {turn_count} 轮。首问：{first_q}"
            )
            _get_session_vectors().index_session(
                session_id=session_id,
                summary_text=summary,
                user_id=request.user_id or "default",
                mode=session_data.get("mode", "single"),
                created_at=session_data.get("created_at", ""),
                total_tokens=session_data.get("total_tokens", 0),
            )
    except Exception as e:
        logger.warning(f"Failed to index session to Milvus: {e}")

    return saved
