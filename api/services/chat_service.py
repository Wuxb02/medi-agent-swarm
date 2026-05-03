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

# 事件持久化目录（与 SessionSummaryManager 一致）
_SUMMARY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "memory", "swarm", "session_summaries"
)


class EventBridge:
    """事件桥接器：拦截 SharedContext 的事件，推送到流"""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, event: Event):
        await self.queue.put(event)


async def chat_non_stream(request: ChatRequest) -> ChatResponse:
    """非流式问答"""
    coordinator = SwarmCoordinator(enable_swarm=request.enable_swarm)
    result = await coordinator.process(
        question=request.question,
        context=request.context,
        session_id=request.session_id
    )
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
    """流式问答（换行分隔 JSON）"""
    bridge = EventBridge()

    session_id = request.session_id or (
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    )

    # 1. 发送 start
    start_event = {"session_id": session_id, "mode": "swarm" if request.enable_swarm else "single"}
    yield _json_line("start", start_event)

    # 收集所有事件用于持久化
    collected_events: List[Dict[str, Any]] = [{"event": "start", "data": start_event}]

    # 2. 创建协调器 + 启动处理
    coordinator = SwarmCoordinator(
        enable_swarm=request.enable_swarm,
        event_callback=lambda event: bridge.queue.put_nowait(event)
    )

    process_task = asyncio.create_task(
        coordinator.process(
            question=request.question,
            context=request.context,
            session_id=session_id
        )
    )

    # 3. 持续从队列读取事件并发送
    while not process_task.done() or not bridge.queue.empty():
        try:
            event = await asyncio.wait_for(bridge.queue.get(), timeout=0.5)
            mapped_type = _map_event_type(event.type.value)
            # Swarm 模式下跳过 content delta（多 Worker 的 token 交错会导致格式混乱，
            # 最终答案由 done 事件携带的完整 answer 提供）
            if request.enable_swarm and mapped_type == "agent_content_delta":
                continue
            event_dict = event.to_dict()
            yield _json_line(mapped_type, event_dict)
            collected_events.append({"event": mapped_type, "data": event_dict})
        except asyncio.TimeoutError:
            continue

    # 4. 获取结果
    try:
        result = await process_task
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        yield _json_line("error", {"error": str(e)})
        return

    # 5. 发送建议
    suggestions = result.get("suggestions", [])
    if suggestions:
        suggestions_payload = {"suggestions": suggestions}
        yield _json_line("suggestions", suggestions_payload)
        collected_events.append({"event": "suggestions", "data": suggestions_payload})

    # 6. 发送 done（最后一个事件）
    done_data = {
        "session_id": result.get("session_id", session_id),
        "total_time": result.get("total_time", 0.0),
        "swarm_metadata": result.get("swarm_metadata", {}),
        "disclaimer": result.get("disclaimer", ""),
        "swarm_enabled": result.get("swarm_enabled", False),
        "agents_involved": result.get("agents_involved", []),
        "answer": result.get("answer", ""),
        "usage": result.get("usage", {}),
    }
    yield _json_line("done", done_data)
    collected_events.append({"event": "done", "data": done_data})

    # 7. 持久化事件到 JSON 文件（后台不阻塞返回）
    try:
        _save_session_events(session_id, collected_events, result)
    except Exception as e:
        logger.warning(f"Failed to save session events: {e}")


def _save_session_events(session_id: str, events: List[Dict[str, Any]], result: Dict[str, Any]):
    """将 SSE 事件列表持久化为 JSON 文件，供历史回放使用"""
    os.makedirs(_SUMMARY_DIR, exist_ok=True)
    filepath = os.path.join(_SUMMARY_DIR, f"session_{session_id}.json")
    # 过滤掉流式内容增量事件（历史回放不需要）
    _SKIP_TYPES = {"agent_content_delta"}
    filtered = [e for e in events if e.get("event") not in _SKIP_TYPES]
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
    }
    return mapping.get(event_type, event_type)
