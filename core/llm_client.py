"""
LLM客户端
支持调用 OpenAI 兼容的 API（如字节跳动豆包、OpenAI、Deepseek 等）
支持 function calling
"""
import os
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


@dataclass
class LLMResponse:
    """LLM 响应数据结构（支持 function calling）"""
    content: Optional[str]
    tool_calls: List[ToolCall]
    finish_reason: str  # "stop", "tool_calls", "length", "content_filter"

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

            self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
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
        异步聊天接口

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 温度参数（可选）
            max_tokens: 最大token数（可选）

        Returns:
            模型返回的文本
        """
        try:
            temperature = temperature or self.temperature
            max_tokens = max_tokens or self.max_tokens

            logger.debug(f"Calling LLM ({self.model_type}) with {len(messages)} messages")
            logger.debug(f"LLM base_url: {self.client.base_url}, model: {self.model_name}")

            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

            content = response.choices[0].message.content
            logger.debug(f"LLM response length: {len(content)} chars")
            return content

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

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
                request_params["tools"] = tools
                if tool_choice != "auto":
                    request_params["tool_choice"] = tool_choice

            response = await self.client.chat.completions.create(**request_params)

            # 解析响应
            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            # 提取工具调用
            tool_calls = []
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments)
                    ))
                logger.debug(f"LLM requested {len(tool_calls)} tool calls")

            return LLMResponse(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=finish_reason
            )

        except Exception as e:
            logger.error(f"LLM call with tools failed: {e}")
            raise

    async def chat_with_tools_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        on_content_token: Optional[Any] = None,
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
            on_tools_detected: 首次检测到 tool_calls 时的回调 fn()

        Returns:
            LLMResponse 对象（与非流式兼容）
        """
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
                request_params["tools"] = tools
                if tool_choice != "auto":
                    request_params["tool_choice"] = tool_choice

            stream = await self.client.chat.completions.create(**request_params)

            content_parts: List[str] = []
            tool_call_accum: Dict[int, Dict[str, Any]] = {}
            finish_reason = "stop"
            tools_notified = False

            async for chunk in stream:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # 累积文本内容
                if delta.content:
                    content_parts.append(delta.content)
                    if on_content_token:
                        on_content_token(delta.content)

                # 累积 tool_calls
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

            # 解析累积的 tool_calls
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

            return LLMResponse(
                content=content,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason
            )

        except Exception as e:
            logger.error(f"Streaming LLM call failed: {e}")
            raise

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
