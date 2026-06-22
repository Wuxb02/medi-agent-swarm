"""
LangGraph 流式适配层

将 LangGraph astream() 输出适配为当前的 SSE NDJSON 格式。
确保：
- thinking token 80ms/20字符批量缓冲（复用现有逻辑）
- 事件类型名称和顺序与当前一致
- 客户端断开检测
"""
import time as _time
from typing import Dict, Any, Optional, Callable, AsyncGenerator
from datetime import datetime
from loguru import logger


class LangGraphStreamAdapter:
    """
    将 LangGraph astream() 输出适配为 SSE NDJSON 行

    使用方式：
        adapter = LangGraphStreamAdapter()
        async for ndjson_line in adapter.adapt_stream(
            graph.astream(initial_state, config, stream_mode=["custom", "messages", "updates"]),
            session_id="...",
            is_disconnected=lambda: False,
        ):
            yield ndjson_line
    """

    def __init__(self):
        # thinking 批量缓冲（复用现有 80ms/20字符逻辑）
        self._think_buf: Dict[str, Dict[str, Any]] = {}
        self._last_think_flush = _time.monotonic()
        self._think_flush_interval = 0.08      # 80ms
        self._think_flush_min_chars = 20        # 20 字符

    async def adapt_stream(
        self,
        astream_iter,                       # graph.astream() 返回的异步迭代器
        session_id: str,
        collected_events: list,             # 输出：收集所有事件用于持久化
        is_disconnected: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        将 astream() 输出转为 NDJSON 行

        Args:
            astream_iter: graph.astream() 异步迭代器
            session_id: 会话 ID
            collected_events: 输出列表，收集所有事件的 (event_name, data) 元组
            is_disconnected: 客户端断开检测回调

        Yields:
            NDJSON 行: {"event": "xxx", "data": {...}}\n
        """
        # 1. 发送 start 事件
        start_data = {"session_id": session_id}
        collected_events.append(("start", start_data))
        yield self._json_line("start", start_data)

        result = None

        try:
            async for chunk in astream_iter:
                # 客户端断开检测
                if is_disconnected and is_disconnected():
                    logger.info(f"客户端断开: session={session_id}")
                    return

                # 处理 chunk
                for event_str in self._process_chunk(chunk, collected_events):
                    yield event_str

        except Exception as e:
            logger.error(f"astream 异常: {e}")
            yield self._json_line("error", {"error": str(e)})
            return

        # 2. 刷新 thinking 缓冲
        for event_str in self._flush_think_buffer(force=True, collected_events=collected_events):
            yield event_str

        # 3. 发送 done 事件（在最终的 update 之后）
        # done 事件需要从 graph state 中提取最终结果
        # 这里由调用方在 astream 完成后手动构造

    def _process_chunk(self, chunk, collected_events: list):
        """处理单个 astream chunk，生成 NDJSON 行"""
        # LangGraph astream 的 chunk 格式因 stream_mode 而异
        # stream_mode="custom" → (mode, data)
        # stream_mode="messages" → (message_chunk, metadata)
        # stream_mode="updates" → (node_name, state_update)

        if not isinstance(chunk, tuple) or len(chunk) < 2:
            return

        mode = chunk[0]
        data = chunk[1]

        if mode == "custom":
            # 自定义事件（agent_thinking, agent_tool_step, agent_start 等）
            if isinstance(data, dict):
                event_type = data.get("event_type", "agent_thinking")
                event_payload = data.get("data", data)

                if event_type == "agent_thinking":
                    # 进入批量缓冲
                    self._buffer_thinking(event_payload)
                else:
                    # 非 thinking 事件，先刷新缓冲再发送
                    yield from self._flush_think_buffer(force=True, collected_events=collected_events)

                    event_dict = {
                        "source_agent": data.get("source_agent", ""),
                        "data": event_payload,
                        "timestamp": datetime.now().isoformat(),
                    }
                    collected_events.append((event_type, event_dict))
                    yield self._json_line(event_type, event_dict)

        elif mode == "messages":
            # LLM token 流式
            # 在 AgentSubGraph 中，流式通过 StreamTokenRouter 处理
            # 这里仅做定期刷新
            if self._think_buf and _time.monotonic() - self._last_think_flush >= 0.08:
                yield from self._flush_think_buffer(force=False, collected_events=collected_events)

        elif mode == "updates":
            # 节点完成更新
            # 刷新 thinking 缓冲
            yield from self._flush_think_buffer(force=True, collected_events=collected_events)

        # 定期刷新（时间驱动）
        if self._think_buf and _time.monotonic() - self._last_think_flush >= self._think_flush_interval:
            yield from self._flush_think_buffer(force=False, collected_events=collected_events)

    def _buffer_thinking(self, payload: Dict):
        """将 thinking token 加入批量缓冲"""
        source_agent = payload.get("source_agent", "")
        iteration = payload.get("data", {}).get("iteration", 0) \
            if "data" in payload else payload.get("iteration", 0)
        content = payload.get("data", {}).get("content", "") \
            if "data" in payload else payload.get("content", "")

        key = f"{source_agent}:{iteration}"
        if key not in self._think_buf:
            self._think_buf[key] = {
                "content": "",
                "agent": source_agent,
                "iteration": iteration,
            }
        self._think_buf[key]["content"] += content

    def _flush_think_buffer(self, force: bool = False, collected_events: list = None):
        """刷新 thinking 缓冲，生成合并的 agent_thinking 事件"""
        now = _time.monotonic()
        for key, entry in list(self._think_buf.items()):
            text = entry["content"]
            if not text:
                del self._think_buf[key]
                continue

            if not force and now - self._last_think_flush < self._think_flush_interval \
                    and len(text) < self._think_flush_min_chars:
                continue

            batch_dict = {
                "source_agent": entry["agent"],
                "data": {"content": text, "iteration": entry["iteration"]},
                "timestamp": datetime.now().isoformat(),
            }
            if collected_events is not None:
                collected_events.append(("agent_thinking", batch_dict))
            yield self._json_line("agent_thinking", batch_dict)
            del self._think_buf[key]

        if force or not self._think_buf:
            self._last_think_flush = now

    @staticmethod
    def _json_line(event_name: str, data: Dict[str, Any]) -> str:
        """单行 JSON: {"event": "xxx", "data": {...}}\n"""
        import json
        return json.dumps(
            {"event": event_name, "data": data},
            ensure_ascii=False,
            default=str,
        ) + "\n"
