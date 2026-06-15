"""
StreamTokenRouter: 流式 Token 路由状态机

管理 LLM 流式响应中的 token 路由，使用显式状态对象替代闭包 list 包装技巧。
确保：
- Content token 和 Reasoning token 正确分离
- 检测到 tool_calls 时自动切换到 Thinking 通道
- 推理内容绝不泄露到 Content 通道
"""
from dataclasses import dataclass, field
from typing import List, Optional, Callable


@dataclass
class RouterState:
    """路由状态"""
    tools_detected: bool = False
    reasoning_active: bool = False
    content_buffer: List[str] = field(default_factory=list)


class StreamTokenRouter:
    """流式 Token 路由状态机

    状态转换:
    INITIAL
      ├─ 收到 content token → CONTENT（推送到 on_content_token）
      ├─ 收到 reasoning token → REASONING（推送到 on_thinking，后续 content 缓存）
      └─ 检测 tools_detected → TOOLS（推送到 on_thinking，清空 content buffer）
    """

    def __init__(
        self,
        on_think: Optional[Callable[[str], None]] = None,
        on_content: Optional[Callable[[str], None]] = None,
    ):
        self._on_think = on_think
        self._on_content = on_content
        self._state = RouterState()

    def reset(self):
        """重置状态机（每次 LLM 调用前）"""
        self._state = RouterState()

    @property
    def tools_detected(self) -> bool:
        return self._state.tools_detected

    @property
    def reasoning_active(self) -> bool:
        return self._state.reasoning_active

    def on_tools_detected(self):
        """流检测到 tool_calls"""
        self._state.tools_detected = True
        self._state.content_buffer.clear()

    def on_content_token(self, token: str):
        """处理 content token"""
        if self._state.tools_detected:
            if self._on_think:
                self._on_think(token)
        elif self._state.reasoning_active:
            self._state.content_buffer.append(token)
        else:
            if self._on_content:
                self._on_content(token)

    def on_reasoning_token(self, token: str):
        """处理 reasoning token"""
        self._state.reasoning_active = True
        if self._on_think:
            self._on_think(token)

    def flush_content_buffer(self):
        """推理结束后，释放缓存的 content token 到正文通道"""
        if self._state.content_buffer and self._on_content:
            for token in self._state.content_buffer:
                self._on_content(token)
            self._state.content_buffer.clear()
