"""P2-16: StreamTokenRouter 单元测试"""
import pytest
from mediZJ.core.stream_token_router import StreamTokenRouter


class TestStreamTokenRouter:
    """测试流式 Token 路由状态机"""

    def test_initial_state(self):
        router = StreamTokenRouter()
        assert router.tools_detected is False
        assert router.reasoning_active is False

    def test_content_token_routed_to_content(self):
        tokens = []
        router = StreamTokenRouter(on_content=tokens.append)
        router.on_content_token("hello")
        assert tokens == ["hello"]

    def test_reasoning_activates_and_routes_to_think(self):
        think_tokens = []
        router = StreamTokenRouter(on_think=think_tokens.append)
        router.on_reasoning_token("thinking...")
        assert router.reasoning_active is True
        assert think_tokens == ["thinking..."]

    def test_content_buffered_during_reasoning(self):
        think_tokens = []
        content_tokens = []
        router = StreamTokenRouter(
            on_think=think_tokens.append,
            on_content=content_tokens.append,
        )
        router.on_reasoning_token("step1:")
        router.on_content_token("result1")
        assert content_tokens == []
        assert think_tokens == ["step1:"]

    def test_flush_content_buffer_after_reasoning(self):
        content_tokens = []
        router = StreamTokenRouter(on_content=content_tokens.append)
        router.on_reasoning_token("think")
        router.on_content_token("cached1")
        router.on_content_token("cached2")
        router.flush_content_buffer()
        assert content_tokens == ["cached1", "cached2"]

    def test_tools_detected_clears_buffer(self):
        think_tokens = []
        content_tokens = []
        router = StreamTokenRouter(
            on_think=think_tokens.append,
            on_content=content_tokens.append,
        )
        # 推理中的 content token 被缓存
        router.on_reasoning_token("step")
        router.on_content_token("cached1")
        router.on_content_token("cached2")
        # 检测到 tool_calls 后清空缓存
        router.on_tools_detected()
        assert router.tools_detected is True
        # 缓存的 token 不应被 flush 到 content
        router.flush_content_buffer()
        assert content_tokens == []  # cached tokens 已被清空

    def test_tools_detected_routes_to_thinking(self):
        think_tokens = []
        content_tokens = []
        router = StreamTokenRouter(
            on_think=think_tokens.append,
            on_content=content_tokens.append,
        )
        router.on_tools_detected()
        router.on_content_token("tool_think_1")
        router.on_content_token("tool_think_2")
        assert think_tokens == ["tool_think_1", "tool_think_2"]
        assert content_tokens == []

    def test_reset_clears_all_state(self):
        router = StreamTokenRouter()
        router.on_tools_detected()
        router.on_reasoning_token("x")
        router.on_content_token("y")
        router.reset()
        assert router.tools_detected is False
        assert router.reasoning_active is False

    def test_no_callbacks_no_crash(self):
        """无回调时不应崩溃"""
        router = StreamTokenRouter()
        router.on_content_token("test")
        router.on_reasoning_token("test")
        router.on_tools_detected()
        router.flush_content_buffer()

    def test_flush_without_reasoning_is_noop(self):
        content_tokens = []
        router = StreamTokenRouter(on_content=content_tokens.append)
        router.on_content_token("direct")
        router.flush_content_buffer()
        assert content_tokens == ["direct"]
