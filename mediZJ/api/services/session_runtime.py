"""会话级运行期（SessionRuntime）— 缓存 graph + checkpointer，支持 interrupt 恢复

LangGraph 的 MemorySaver 是内存态：interrupt 挂起后，第二次
ainvoke(Command(resume=...)) 必须复用同一个图对象 + 同一个 checkpointer，
否则线程状态丢失（LangGraphException: INTERRUPT）。

因此每个会话（SSE 流）持有一个 SessionRuntime：graph / memory_saver / config
三件套同生命周期。SSE 流结束时 release() 清理，防内存泄漏。
"""
import time
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Callable
from loguru import logger

from mediZJ.swarm.swarm_coordinator import SwarmCoordinator


@dataclass
class SessionRuntime:
    """一次会话问答所需的运行期状态

    graph 为编译后的 SupervisorGraph（内部 MemorySaver 由 coordinator.build_graph
    构建），interrupt 挂起/恢复必须在同一 runtime 内完成。
    """
    coordinator: SwarmCoordinator
    graph: Any                       # CompiledStateGraph
    config: Dict[str, Any]           # {"configurable": {"thread_id": session_id}}
    initial_state: Dict[str, Any]    # SupervisorState 初始状态
    build_time: float = field(default_factory=time.time)
    session_id: str = field(default="")


class SessionRuntimeRegistry:
    """每会话缓存 SessionRuntime，上限 LRU 淘汰（与 QuestionnaireManager 注册表同模式）"""

    def __init__(self, max_entries: int = 1000, ttl_seconds: float = 600.0):
        self._runtimes: Dict[str, SessionRuntime] = {}
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds

    def acquire(self, session_id: str) -> Optional[SessionRuntime]:
        """获取会话运行期（不存在返回 None）"""
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            return None
        runtime.build_time = time.time()  # 刷新活动时间
        return runtime

    def store(self, runtime: SessionRuntime) -> None:
        """存入会话运行期（惰性 LRU：超上限时淘汰最久未活动项）"""
        if len(self._runtimes) >= self._max_entries:
            lru_sid = min(self._runtimes, key=lambda s: self._runtimes[s].build_time)
            logger.warning(f"SessionRuntime LRU eviction: {lru_sid}")
            self._runtimes.pop(lru_sid, None)
        self._runtimes[runtime.session_id] = runtime

    def release(self, session_id: str) -> None:
        """释放会话运行期（SSE 流结束/断开时调用）"""
        self._runtimes.pop(session_id, None)


# 全局会话运行期注册表（单事件循环，同步代码段内访问安全）
_runtimes_registry = SessionRuntimeRegistry()

# 会话级问卷答案信号队列（session_id -> asyncio.Queue）
# interrupt 挂起期间，POST /api/chat/answer 将答案放入队列，
# SSE 主循环消费后用 Command(resume=...) 驱动图恢复。
_answer_queues: Dict[str, asyncio.Queue] = {}


def get_runtime(session_id: str) -> Optional[SessionRuntime]:
    """获取会话运行期"""
    return _runtimes_registry.acquire(session_id)


def store_runtime(runtime: SessionRuntime) -> None:
    """存入会话运行期"""
    _runtimes_registry.store(runtime)


def release_runtime(session_id: str) -> None:
    """释放会话运行期"""
    _runtimes_registry.release(session_id)


def get_answer_queue(session_id: str) -> asyncio.Queue:
    """获取（或创建）会话的问卷答案信号队列"""
    queue = _answer_queues.get(session_id)
    if queue is None:
        queue = asyncio.Queue()
        _answer_queues[session_id] = queue
    return queue


def put_answer(session_id: str, answers: Dict[str, Any]) -> bool:
    """将用户答案放入会话信号队列（由 POST /api/chat/answer 调用）

    Returns:
        True 表示成功入队；False 表示会话无活动信号队列（可能已清理）
    """
    queue = _answer_queues.get(session_id)
    if queue is None:
        logger.warning(f"无活动答案队列 (session={session_id})")
        return False
    queue.put_nowait(answers)
    return True


def clear_answer_queue(session_id: str) -> None:
    """清理会话答案队列（SSE 流结束/断开时调用）"""
    _answer_queues.pop(session_id, None)
