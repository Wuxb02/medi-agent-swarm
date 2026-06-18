"""Trace Span 收集器（单例，线程安全）"""
import asyncio
import threading
from typing import Dict, List, Optional, Callable, Any
from loguru import logger
from .models import Span, SpanType


class TraceCollector:
    """收集所有 span，flush 时构建树写入 SQLite

    生命周期:
    1. begin_trace() 请求开始
    2. span 通过 traced_span 上下文管理器自动收集
    3. flush() 请求结束 -> 写入存储
    """

    _instance: Optional["TraceCollector"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._spans: Dict[str, List[Span]] = {}  # trace_id -> span 列表
        self._storage = None  # SQLite 存储后端
        self._write_lock = asyncio.Lock()  # 并发保护 _spans 写入
        self._callbacks: Dict[str, List[Callable[[Span], Any]]] = {}  # trace_id -> 回调列表

    @classmethod
    def reset(cls):
        """重置单例（测试用）"""
        cls._instance = None

    def set_storage(self, storage):
        """设置存储后端"""
        self._storage = storage

    def add_span_callback(self, trace_id: str, callback: Callable[[Span], Any]):
        """注册 span 完成回调（按 trace_id 路由，支持并发请求）"""
        if trace_id not in self._callbacks:
            self._callbacks[trace_id] = []
        self._callbacks[trace_id].append(callback)

    def remove_span_callback(self, trace_id: str, callback: Callable[[Span], Any]):
        """移除 span 完成回调"""
        cbs = self._callbacks.get(trace_id, [])
        if callback in cbs:
            cbs.remove(callback)
        if not cbs:
            self._callbacks.pop(trace_id, None)

    def begin_trace(self, trace_id: str):
        """开始新 trace，创建并收集根 span"""
        self._spans[trace_id] = []
        root = Span(
            id=trace_id,
            trace_id=trace_id,
            span_type=SpanType.TRACE,
            name="request",
        )
        self._spans[trace_id].append(root)
        logger.debug(f"[Trace] Root span created for {trace_id[:12]}...")

    def collect(self, span: Span):
        """收集已完成的 span（由 traced_span.__exit__ 调用）

        list.append 在 CPython GIL 下是原子操作，与 flush 的 pop 配合安全。
        """
        if not span.trace_id:
            return
        if span.trace_id not in self._spans:
            self._spans[span.trace_id] = []
        self._spans[span.trace_id].append(span)
        logger.debug(
            f"[Trace] {span.span_type.value}/{span.name} "
            f"({span.timing.duration_ms:.0f}ms)" if span.timing.duration_ms else
            f"[Trace] {span.span_type.value}/{span.name}"
        )
        # 实时回调（用于 SSE 推送），按 trace_id 路由
        for cb in self._callbacks.get(span.trace_id, []):
            try:
                cb(span)
            except Exception:
                pass

    async def flush(self, trace_id: str):
        """构建树并写入存储"""
        async with self._write_lock:
            spans = self._spans.pop(trace_id, [])
        if not spans:
            return

        root = self._build_tree(spans)

        if self._storage:
            try:
                self._storage.save(root, spans)
            except Exception as e:
                logger.error(f"[Trace] 存储写入失败: {e}")

        # 清理回调
        self._callbacks.pop(trace_id, None)

        logger.info(f"[Trace] 已保存 {len(spans)} 个 span (trace={trace_id[:12]}...)")

    def get_flat_spans(self, trace_id: str) -> List[Span]:
        """获取指定 trace 的所有 span（扁平列表）"""
        return self._spans.get(trace_id, [])

    def _build_tree(self, spans: List[Span]) -> Span:
        """从扁平列表重建 span 树"""
        span_map = {s.id: s for s in spans}
        root = None
        for span in spans:
            if span.parent_id and span.parent_id in span_map:
                span_map[span.parent_id].children.append(span)
            elif span.span_type == SpanType.TRACE:
                root = span
        # 如果没有 TRACE 类型，找 parent_id 不在 span_map 中的 span 作为根
        if root is None:
            for span in spans:
                if span.parent_id is None or span.parent_id not in span_map:
                    root = span
                    break
        return root or spans[0]
