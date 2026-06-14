"""问答服务：封装 process_with_swarm + 流式事件推送"""
import asyncio
import json
import os
import uuid
from typing import Dict, Any, Optional, AsyncGenerator, List
from datetime import datetime
from loguru import logger

from swarm.swarm_coordinator import SwarmCoordinator
from swarm.events import Event
from api.models.chat import ChatRequest, ChatResponse
from core.questionnaire_manager import QuestionnaireManager

# 事件持久化目录（与 SessionSummaryManager 一致）
_SUMMARY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "memory", "swarm", "session_summaries"
)

# SQLite + Milvus 持久化
from memory.session_db import SessionDB
from memory.session_vector_store import SessionVectorStore

_session_db = SessionDB()
_session_vectors = SessionVectorStore()

# Trace 惰性导入
try:
    from trace import TraceCollector
    _TRACE_AVAIL = True
except ImportError:
    _TRACE_AVAIL = False

# 问卷管理器注册表（session_id → QuestionnaireManager）
_managers: Dict[str, QuestionnaireManager] = {}


def get_manager(session_id: str) -> QuestionnaireManager:
    """获取或创建会话的问卷管理器"""
    if session_id not in _managers:
        _managers[session_id] = QuestionnaireManager()
    return _managers[session_id]


def remove_manager(session_id: str):
    """清理会话的问卷管理器"""
    manager = _managers.pop(session_id, None)
    if manager:
        manager.cancel_all()


class EventBridge:
    """事件桥接器：拦截 SharedContext 的事件，推送到流"""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: Event):
        await self.queue.put(event)


async def chat_non_stream(request: ChatRequest) -> ChatResponse:
    """非流式问答"""
    session_id = request.session_id or (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    )
    manager = get_manager(session_id)
    coordinator = SwarmCoordinator(
        questionnaire_manager=manager
    )
    result = await coordinator.process(
        question=request.question,
        context=request.context,
        session_id=session_id
    )

    # 等待长期记忆保存完成（最多 30 秒，超时降级不阻塞）
    ltm_task = result.get('_ltm_save_task')
    if ltm_task and not ltm_task.done():
        try:
            await asyncio.wait_for(ltm_task, timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(f"LTM save timeout in non-stream for session={session_id}")
        except Exception as e:
            logger.error(f"LTM save error in non-stream: {e}")

    # 持久化到 SQLite + Milvus
    persist_session_id = result.get("session_id", session_id)
    if persist_session_id:
        try:
            _persist_session_turn(persist_session_id, request, result, [])
        except Exception as e:
            logger.warning(f"Failed to persist non-stream session turn: {e}")

    # 清理问卷管理器
    remove_manager(session_id)

    return ChatResponse(
        answer=result.get("answer", ""),
        suggestions=result.get("suggestions", []),
        disclaimer=result.get("disclaimer", ""),
        session_id=result.get("session_id", ""),
        swarm_enabled=result.get("swarm_enabled", False),
        agents_involved=result.get("agents_involved", []),
        subtasks_completed=result.get("subtasks_completed", 0),
        total_time=result.get("total_time", 0.0),
        swarm_metadata=result.get("swarm_metadata", {}),
        timeout_occurred=result.get("timeout_occurred", False),
        usage=result.get("usage", {}),
    )


async def chat_stream(request: ChatRequest) -> AsyncGenerator[str, None]:
    """流式问答（换行分隔 JSON）

    支持客户端断开时自动取消后台处理任务，避免浪费 LLM token。
    """
    bridge = EventBridge()

    session_id = request.session_id or (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    )

    # 1. 发送 start
    start_event = {"session_id": session_id}
    yield _json_line("start", start_event)

    # 收集所有事件用于持久化
    collected_events: List[Dict[str, Any]] = [{"event": "start", "data": start_event}]

    # 1.5 创建会话的问卷管理器
    manager = get_manager(session_id)

    # 1.6 注册 Trace 实时推送回调（span 完成时入队 EventBridge）
    if _TRACE_AVAIL:
        collector = TraceCollector()

        def _on_span_complete(span):
            from swarm.events import Event, EventType
            bridge.queue.put_nowait(Event(
                type=EventType.TRACE_SPAN,
                source_agent="trace",
                data={"id": span.id, "trace_id": span.trace_id, "parent_id": span.parent_id,
                      "span_type": span.span_type.value,
                      "name": span.name, "start_time": span.timing.start_time.isoformat(),
                      "duration_ms": span.timing.duration_ms, "status": span.status.value}
            ))

        collector.add_span_callback(session_id, _on_span_complete)

    # 2. 创建协调器 + 启动处理
    coordinator = SwarmCoordinator(
        event_callback=lambda event: bridge.queue.put_nowait(event),
        questionnaire_manager=manager
    )

    process_task = asyncio.create_task(
        coordinator.process(
            question=request.question,
            context=request.context,
            session_id=session_id
        )
    )

    # 2.5 启动客户端断开检测任务
    async def _wait_for_disconnect():
        """等待客户端断开连接"""
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.1)

    disconnect_task = asyncio.create_task(_wait_for_disconnect())

    # 3. 持续从队列读取事件并发送（同时监控断开和完成）
    result = None
    disconnected = False
    while not process_task.done() and not disconnect_task.done():
        done, _ = await asyncio.wait(
            [process_task, disconnect_task],
            timeout=0.5,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnect_task in done:
            disconnected = True
            break
        if process_task in done:
            break
        # 从 bridge 队列读取事件并 yield
        try:
            event = await asyncio.wait_for(bridge.queue.get(), timeout=0.1)
            mapped_type = _map_event_type(event.type.value)
            if mapped_type == "agent_content_delta":
                continue
            event_dict = event.to_dict()
            if event.type.value == "agent_questionnaire":
                yield _json_line("agent_questionnaire", event_dict)
                collected_events.append({"event": "agent_questionnaire", "data": event_dict})
                continue
            yield _json_line(mapped_type, event_dict)
            collected_events.append({"event": mapped_type, "data": event_dict})
        except asyncio.TimeoutError:
            continue

    # 4. 处理断开情况
    if disconnected:
        logger.info(f"Client disconnected for session={session_id}, cancelling processing")
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            logger.info(f"Processing cancelled for session={session_id}")
        except Exception as e:
            logger.error(f"Error after cancel for session={session_id}: {e}")
        remove_manager(session_id)
        return

    # 5. 正常完成：等待 process_task 结果
    disconnect_task.cancel()
    try:
        result = process_task.result()
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        yield _json_line("error", {"error": str(e)})
        return

    # 6. 发送建议
    suggestions = result.get("suggestions", [])
    if suggestions:
        suggestions_payload = {"suggestions": suggestions}
        yield _json_line("suggestions", suggestions_payload)
        collected_events.append({"event": "suggestions", "data": suggestions_payload})

    # 6.5 持久化到 SQLite + Milvus（在 done 之前，确保前端 loadSessions 能查到）
    try:
        _persist_session_turn(session_id, request, result, collected_events)
    except Exception as e:
        logger.warning(f"Failed to persist session turn: {e}")

    # 7. 发送 done（最后一个事件）
    done_data = {
        "session_id": result.get("session_id", session_id),
        "total_time": result.get("total_time", 0.0),
        "swarm_metadata": result.get("swarm_metadata", {}),
        "disclaimer": result.get("disclaimer", ""),
        "swarm_enabled": result.get("swarm_enabled", False),
        "agents_involved": result.get("agents_involved", []),
        "answer": result.get("answer", ""),
        "usage": result.get("usage", {}),
        "performance_metrics": result.get("performance_metrics", {}),
    }
    yield _json_line("done", done_data)
    collected_events.append({"event": "done", "data": done_data})

    # 7.5 等待 LTM 保存任务完成（done 已 yield，客户端已收到数据）
    ltm_task = getattr(coordinator, 'ltm_save_task', None)
    if ltm_task and not ltm_task.done():
        try:
            await asyncio.wait_for(ltm_task, timeout=90.0)
            logger.info(f"LTM save completed for session={session_id}")
        except asyncio.TimeoutError:
            logger.warning(f"LTM save timeout in chat_stream for session={session_id}")
        except Exception as e:
            logger.error(f"LTM save error in chat_stream: {e}")

    # 8. 持久化事件到 JSON 文件（后台不阻塞返回）
    try:
        _save_session_events(session_id, collected_events, result)
    except Exception as e:
        logger.warning(f"Failed to save session events: {e}")

    # 9. 清理问卷管理器
    remove_manager(session_id)

    # 10. 清理 trace 回调（确保不留残留引用；正常流程 flush 已清理）
    if _TRACE_AVAIL:
        collector.remove_span_callback(session_id, _on_span_complete)


def _merge_thinking_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将流式 agent_thinking token 事件按 (source_agent, iteration) 合并为完整文本"""
    merged: List[Dict[str, Any]] = []
    buf_agent: Optional[str] = None
    buf_iteration: Optional[int] = None
    buf_parts: List[str] = []
    buf_first_ts: Optional[str] = None

    def _flush():
        """将缓冲区中的 token 拼接为一条完整事件"""
        if not buf_parts:
            return
        merged.append({
            "event": "agent_thinking",
            "data": {
                "source_agent": buf_agent,
                "content": "".join(buf_parts),
                "iteration": buf_iteration,
                "timestamp": buf_first_ts,
            }
        })

    for ev in events:
        if ev.get("event") != "agent_thinking":
            _flush()
            buf_parts.clear()
            buf_agent = buf_iteration = buf_first_ts = None
            merged.append(ev)
            continue

        data = ev.get("data", {})
        agent = data.get("source_agent")
        iteration = data.get("data", {}).get("iteration") if "data" in data else data.get("iteration")

        if agent == buf_agent and iteration == buf_iteration:
            # 同一轮 thinking，追加 token
            content = data.get("data", {}).get("content") if "data" in data else data.get("content", "")
            buf_parts.append(content)
        else:
            # 新的一轮 thinking，先 flush 旧的
            _flush()
            buf_agent = agent
            buf_iteration = iteration
            content = data.get("data", {}).get("content") if "data" in data else data.get("content", "")
            buf_parts = [content]
            buf_first_ts = data.get("timestamp") or (data.get("data", {}).get("timestamp") if "data" in data else None)

    _flush()
    return merged


def _save_session_events(session_id: str, events: List[Dict[str, Any]], result: Dict[str, Any]):
    """将 SSE 事件列表持久化为 JSON 文件，供历史回放使用"""
    os.makedirs(_SUMMARY_DIR, exist_ok=True)
    filepath = os.path.join(_SUMMARY_DIR, f"session_{session_id}.json")
    # 过滤掉流式内容增量事件（历史回放不需要）
    _SKIP_TYPES = {"agent_content_delta"}
    filtered = [e for e in events if e.get("event") not in _SKIP_TYPES]
    # 合并流式 thinking token 为完整文本
    filtered = _merge_thinking_events(filtered)
    payload = {
        "session_id": session_id,
        "events": filtered,
        "suggestions": result.get("suggestions", []),
        "disclaimer": result.get("disclaimer", ""),
        "answer": result.get("answer", ""),
        "agents_involved": result.get("agents_involved", []),
        "subtasks_completed": result.get("subtasks_completed", 0),
        "total_time": result.get("total_time", 0.0),
        "swarm_enabled": result.get("swarm_enabled", False),
        "usage": result.get("usage", {}),
        "performance_metrics": result.get("performance_metrics", {}),
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

    _session_db.save_turn(
        session_id=session_id,
        turn_index=turn_index,
        user_msg={
            "role": "user",
            "content": request.question,
            "timestamp": now,
        },
        assistant_msg={
            "role": "assistant",
            "content": result.get("answer", ""),
            "timestamp": now,
            "agent_events": filtered_events,
            "suggestions": result.get("suggestions", []),
            "disclaimer": result.get("disclaimer", ""),
            "agents_involved": result.get("agents_involved", []),
            "total_time": result.get("total_time", 0.0),
            "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            "subtasks_completed": result.get("subtasks_completed", 0),
            "mode": "swarm" if result.get("swarm_enabled", False) else "single",
            "parallel_efficiency": result.get("performance_metrics", {}).get("parallel_efficiency", 0),
            "information_coverage": result.get("performance_metrics", {}).get("information_coverage", 0),
            "redundancy": result.get("performance_metrics", {}).get("redundancy", 0),
        },
    )

    # 索引到 Milvus（每次更新摘要，用于语义搜索）
    try:
        session_data = _session_db.get_session(session_id)
        if session_data:
            messages = session_data.get("messages", [])
            user_msgs = [m for m in messages if m["role"] == "user"]
            first_q = user_msgs[0]["content"][:100] if user_msgs else ""
            turn_count = session_data.get("turn_count", 0)
            summary = (
                f"会话共 {turn_count} 轮。首问：{first_q}"
            )
            _session_vectors.index_session(
                session_id=session_id,
                summary_text=summary,
                mode=session_data.get("mode", "single"),
                created_at=session_data.get("created_at", ""),
                total_tokens=session_data.get("total_tokens", 0),
            )
    except Exception as e:
        logger.warning(f"Failed to index session to Milvus: {e}")
