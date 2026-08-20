"""
LLM客户端
支持调用 OpenAI 兼容的 API（如字节跳动豆包、OpenAI、Deepseek 等）
支持 function calling
"""
import os
import re
import asyncio
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from openai import AsyncOpenAI
from loguru import logger


@dataclass
class ToolCall:
    """Function call 数据结构"""
    id: str
    name: str
    arguments: Dict[str, Any]


# 进程级共享的 AsyncOpenAI 客户端：httpx 连接池全进程复用，
# 避免每请求新建连接池导致高并发下连接数膨胀
_shared_openai_clients: Dict[Any, AsyncOpenAI] = {}

# LLM 全局并发上限（保护上游 API 配额），惰性初始化以读取环境变量
_llm_semaphore: Optional[asyncio.Semaphore] = None


def _get_shared_openai_client(api_key: str, base_url: str) -> AsyncOpenAI:
    """获取（或创建）共享的 AsyncOpenAI 客户端"""
    key = (base_url, api_key)
    client = _shared_openai_clients.get(key)
    if client is None:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(os.getenv("LLM_TIMEOUT", "60")),
            max_retries=2,
        )
        _shared_openai_clients[key] = client
    return client


def _get_llm_semaphore() -> asyncio.Semaphore:
    """获取 LLM 并发限制信号量"""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(
            int(os.getenv("LLM_MAX_CONCURRENCY", "16"))
        )
    return _llm_semaphore


@dataclass
class LLMResponse:
    """LLM 响应数据结构（支持 function calling）"""
    content: Optional[str]
    tool_calls: List[ToolCall]
    finish_reason: str  # "stop", "tool_calls", "length", "content_filter"
    reasoning_content: Optional[str] = None  # 模型原生推理内容（如 GLM-4.7、DeepSeek-R1）
    usage: Optional[Dict[str, Any]] = None

    def has_tool_calls(self) -> bool:
        """是否包含 function calls"""
        return len(self.tool_calls) > 0


class LLMClient:
    """统一的LLM客户端，支持多种模型"""

    def __init__(self, model_type: str = "openai_compatible"):
        """
        初始化LLM客户端

        Args:
            model_type: 模型类型，默认 "openai_compatible"（支持 OpenAI 兼容的 API）
        """
        self.model_type = model_type

        if model_type == "openai_compatible":
            # 使用 OpenAI 兼容的 API（通过 .env 环境变量配置）
            api_key = os.getenv("LLM_API_KEY")
            base_url = os.getenv("LLM_BASE_URL")
            if not api_key or not base_url:
                raise ValueError("LLM_API_KEY 或 LLM_BASE_URL 未设置，请在 .env 文件中配置")

            self.client = _get_shared_openai_client(api_key, base_url)
            self.model_name = os.getenv("LLM_MODEL_NAME", "gpt-4o")
            self.temperature = float(os.getenv("LLM_TEMPERATURE", "0.7"))
            self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "8192"))
        else:
            raise ValueError(f"Unknown model type: {model_type}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        异步聊天接口（纯文本场景，委托 chat_with_tools 并提取 .content）

        走 chat_with_tools(tools=None) 统一链路，自动获得 _sanitize_content()
        XML 清洗，防止模型将 tool-call 格式输出为原始文本泄露到回答中。
        """
        response = await self.chat_with_tools(
            messages=messages,
            tools=None,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
        return response.content or ""

    async def chat_with_retry(
        self,
        messages: List[Dict[str, str]],
        max_retries: int = 3,
        **kwargs
    ) -> str:
        """
        带重试的聊天接口

        Args:
            messages: 消息列表
            max_retries: 最大重试次数

        Returns:
            模型返回的文本
        """
        for attempt in range(max_retries):
            try:
                return await self.chat(messages, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"Retry {attempt + 1}/{max_retries} after error: {e}")
                await asyncio.sleep(2 ** attempt)  # 指数退避

    def create_message(self, role: str, content: str) -> Dict[str, str]:
        """
        创建消息对象

        Args:
            role: 角色，"user" 或 "assistant" 或 "system"
            content: 消息内容

        Returns:
            消息字典
        """
        return {"role": role, "content": content}

    # Regex: 匹配 Anthropic 格式的 invoke/parameter XML 标签（含换行），防止模型将其作为文本输出
    _TOOL_CALL_XML_RE = re.compile(
        r'<invoke\s+name="[^"]*"\s*>.*?</invoke>',
        re.DOTALL,
    )

    @staticmethod
    def _sanitize_content(content: Optional[str]) -> Optional[str]:
        """清洗 content 中的原始工具调用 XML 标签

        某些模型（如通过 OpenAI 兼容代理接入的 Anthropic Claude）可能将
        function calling 输出为 <invoke name="...">...</invoke> 原始 XML 文本混入 content，
        而非通过原生 tool_calls 字段。这会导致最终回答中出现无意义的工具调用标记。

        此方法在解析阶段剥离这些 XML，确保它们不会泄露到：
        - 最终回答（AgentLoop 情况2）
        - thinking 文本（AgentLoop 第 238 行）
        - 对话历史（_create_assistant_message_with_tools）
        """
        if not content:
            return content
        cleaned = LLMClient._TOOL_CALL_XML_RE.sub('', content).strip()
        return cleaned or None

    @staticmethod
    def _parse_response(response) -> LLMResponse:
        """解析 OpenAI 响应为 LLMResponse"""
        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        tool_calls = []
        if hasattr(message, 'tool_calls') and message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments)
                ))

        reasoning_content = getattr(message, 'reasoning_content', None)

        usage = None
        if hasattr(response, 'usage') and response.usage:
            details = getattr(response.usage, "prompt_tokens_details", None)
            cached_tokens = (
                getattr(details, "cached_tokens", None)
                if details is not None
                else None
            )
            prompt_tokens = response.usage.prompt_tokens
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
                "cached_prompt_tokens": cached_tokens,
                "cache_hit_ratio": (
                    cached_tokens / prompt_tokens
                    if cached_tokens is not None and prompt_tokens
                    else None
                ),
            }

        return LLMResponse(
            content=LLMClient._sanitize_content(message.content),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            reasoning_content=reasoning_content,
            usage=usage,
        )

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """
        带工具支持的聊天接口

        Args:
            messages: 消息列表
            tools: 工具定义列表（OpenAI format）
            tool_choice: 工具选择策略 ("auto"/"required"/"none")
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            LLMResponse 对象
        """
        try:
            from mediZJ.trace.context import traced_span
            from mediZJ.trace.models import SpanType, LLMAttributes as LLMAttrs
        except ImportError:
            traced_span = None

        try:
            temperature = temperature or self.temperature
            max_tokens = max_tokens or self.max_tokens

            logger.debug(f"Calling LLM with {len(tools) if tools else 0} tools")

            # 准备请求参数
            request_params = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                **kwargs
            }

            # 添加工具参数（如果提供）
            if tools:
                from mediZJ.memory.prompt_prefix import canonical_tools

                request_params["tools"] = canonical_tools(tools)
                if tool_choice != "auto":
                    request_params["tool_choice"] = tool_choice

            if traced_span:
                with traced_span(SpanType.LLM, name="chat_with_tools") as span:
                    span.llm_attrs = LLMAttrs(model=self.model_name)
                    async with _get_llm_semaphore():
                        response = await self.client.chat.completions.create(**request_params)
                    llm_response = self._parse_response(response)
                    if llm_response.usage:
                        span.llm_attrs.prompt_tokens = llm_response.usage.get("prompt_tokens", 0)
                        span.llm_attrs.completion_tokens = llm_response.usage.get("completion_tokens", 0)
                        span.llm_attrs.total_tokens = llm_response.usage.get("total_tokens", 0)
                        span.llm_attrs.cached_prompt_tokens = llm_response.usage.get(
                            "cached_prompt_tokens"
                        )
                        span.llm_attrs.cache_hit_ratio = llm_response.usage.get(
                            "cache_hit_ratio"
                        )
                    span.llm_attrs.finish_reason = llm_response.finish_reason
                    span.llm_attrs.output_content_summary = llm_response.content or ""
                    return llm_response
            else:
                async with _get_llm_semaphore():
                    response = await self.client.chat.completions.create(**request_params)
                return self._parse_response(response)

        except Exception as e:
            logger.error(f"LLM call with tools failed: {e}")
            raise

    async def chat_with_tools_retry(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs
    ) -> LLMResponse:
        """带指数退避重试的工具调用

        - HTTP 4xx（除 429）: 不重试
        - HTTP 5xx / 网络错误 / 超时 / 429: 重试
        - 指数退避: base_delay * 2^attempt

        Args:
            max_retries: 最大重试次数（含首次，共 max_retries 次尝试）
            base_delay: 基础退避延迟（秒）
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                return await self.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs
                )
            except Exception as e:
                last_error = e
                if not _is_retryable(e):
                    raise
                if attempt == max_retries - 1:
                    logger.error(
                        f"LLM call failed after {max_retries} retries: {e}"
                    )
                    raise
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"LLM call retry {attempt + 1}/{max_retries - 1} "
                    f"after {delay:.1f}s: {e}"
                )
                await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]

    async def chat_with_tools_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        on_content_token: Optional[Any] = None,
        on_reasoning_token: Optional[Any] = None,
        on_tools_detected: Optional[Any] = None,
        **kwargs
    ) -> LLMResponse:
        """
        流式聊天接口，逐 token 回调

        Args:
            messages: 消息列表
            tools: 工具定义列表
            tool_choice: 工具选择策略
            on_content_token: 内容 token 回调 fn(token: str)
            on_reasoning_token: 推理内容 token 回调 fn(token: str)
            on_tools_detected: 首次检测到 tool_calls 时的回调 fn()

        Returns:
            LLMResponse 对象（与非流式兼容）
        """
        try:
            from mediZJ.trace.context import traced_span
            from mediZJ.trace.models import SpanType, LLMAttributes as LLMAttrs
        except ImportError:
            traced_span = None

        try:
            temperature = temperature or self.temperature
            max_tokens = max_tokens or self.max_tokens

            request_params = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
                "stream_options": {"include_usage": True},
                **kwargs
            }

            if tools:
                from mediZJ.memory.prompt_prefix import canonical_tools

                request_params["tools"] = canonical_tools(tools)
                if tool_choice != "auto":
                    request_params["tool_choice"] = tool_choice

            if traced_span:
                async with traced_span(SpanType.LLM, name="chat_with_tools_stream") as span:
                    span.llm_attrs = LLMAttrs(model=self.model_name)
                    # 流式期间持续持有信号量（一条流占用一个上游连接）
                    async with _get_llm_semaphore():
                        result = await self._stream_chunks(
                            request_params, on_content_token, on_reasoning_token, on_tools_detected
                        )
                    if result.usage:
                        span.llm_attrs.prompt_tokens = result.usage.get("prompt_tokens", 0)
                        span.llm_attrs.completion_tokens = result.usage.get("completion_tokens", 0)
                        span.llm_attrs.total_tokens = result.usage.get("total_tokens", 0)
                        span.llm_attrs.cached_prompt_tokens = result.usage.get(
                            "cached_prompt_tokens"
                        )
                        span.llm_attrs.cache_hit_ratio = result.usage.get(
                            "cache_hit_ratio"
                        )
                    span.llm_attrs.finish_reason = result.finish_reason
                    span.llm_attrs.output_content_summary = result.content or ""
                    return result
            else:
                async with _get_llm_semaphore():
                    return await self._stream_chunks(
                        request_params, on_content_token, on_reasoning_token, on_tools_detected
                    )

        except Exception as e:
            logger.error(f"Streaming LLM call failed: {e}")
            raise

    async def _stream_chunks(
        self, request_params: Dict[str, Any],
        on_content_token=None, on_reasoning_token=None, on_tools_detected=None
    ) -> LLMResponse:
        """流式读取 + 解析 chunks"""
        stream = await self.client.chat.completions.create(**request_params)

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_call_accum: Dict[int, Dict[str, Any]] = {}
        finish_reason = "stop"
        tools_notified = False
        usage = None

        # 流式 XML 过滤状态机：防止模型将 tool call 输出为原始 <invoke> XML 文本
        _xml_suppress = False
        _xml_buf = ""  # 滚动窗口，跨 token 检测 <invoke / </invoke>

        async for chunk in stream:
            if hasattr(chunk, 'usage') and chunk.usage:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if choice.finish_reason:
                finish_reason = choice.finish_reason

            if delta.content:
                content_parts.append(delta.content)
                if on_content_token:
                    token = delta.content
                    _xml_buf += token
                    # 保持滚动窗口不超过 256 字符，避免内存无限增长
                    if len(_xml_buf) > 256:
                        _xml_buf = _xml_buf[-128:]
                    if not _xml_suppress and '<invoke' in _xml_buf:
                        _xml_suppress = True
                    if _xml_suppress:
                        if '</invoke>' in _xml_buf:
                            _xml_suppress = False
                            _xml_buf = ""
                    else:
                        on_content_token(token)

            if getattr(delta, 'reasoning_content', None):
                reasoning_parts.append(delta.reasoning_content)
                if on_reasoning_token:
                    on_reasoning_token(delta.reasoning_content)

            if delta.tool_calls:
                if not tools_notified and on_tools_detected:
                    on_tools_detected()
                    tools_notified = True
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {"id": "", "name": "", "arguments": ""}

                    entry = tool_call_accum[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

        parsed_tool_calls = []
        for idx in sorted(tool_call_accum.keys()):
            entry = tool_call_accum[idx]
            try:
                args = json.loads(entry["arguments"]) if entry["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            parsed_tool_calls.append(ToolCall(
                id=entry["id"],
                name=entry["name"],
                arguments=args
            ))

        content = "".join(content_parts) or None
        reasoning_content = "".join(reasoning_parts) or None

        return LLMResponse(
            content=LLMClient._sanitize_content(content),
            tool_calls=parsed_tool_calls,
            finish_reason=finish_reason,
            reasoning_content=reasoning_content,
            usage=usage
        )

    def create_tool_message(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        创建工具执行结果消息

        Args:
            tool_call_id: 工具调用ID
            tool_name: 工具名称
            result: 工具执行结果

        Returns:
            工具消息字典
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(result, ensure_ascii=False)
        }


def _is_retryable(error: Exception) -> bool:
    """判断 LLM 调用异常是否可重试

    - HTTP 429 (Rate Limit): 可重试
    - HTTP 5xx / 网络错误 / 超时: 可重试
    - HTTP 4xx (除 429): 不可重试（客户端错误）
    """
    error_str = str(error).lower()

    # 429 Rate Limit 可重试
    if '429' in error_str:
        return True

    # 4xx 客户端错误不重试
    _non_retryable_codes = ['400', '401', '403', '404', '422']
    if any(code in error_str for code in _non_retryable_codes):
        return False

    # 5xx / 网络 / 超时 可重试
    _retryable_keywords = [
        '500', '502', '503', '504',
        'timeout', 'connection', 'reset', 'refused',
        'internal', 'unavailable', 'rate limit',
    ]
    return any(kw in error_str for kw in _retryable_keywords)
