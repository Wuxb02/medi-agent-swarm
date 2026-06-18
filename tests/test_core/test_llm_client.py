"""test_core/test_llm_client.py — LLMClient 单元测试"""

import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch

from mediZJ.core.llm_client import LLMClient, LLMResponse, ToolCall
from tests.helpers import make_mock_openai_response


class TestLLMResponse:
    def test_has_tool_calls_true(self):
        resp = LLMResponse(
            content=None,
            tool_calls=[ToolCall(id="1", name="test", arguments={})],
            finish_reason="tool_calls",
        )
        assert resp.has_tool_calls() is True

    def test_has_tool_calls_false(self):
        resp = LLMResponse(content="hello", tool_calls=[], finish_reason="stop")
        assert resp.has_tool_calls() is False

    def test_reasoning_content(self):
        resp = LLMResponse(
            content="answer",
            tool_calls=[],
            finish_reason="stop",
            reasoning_content="Let me think...",
        )
        assert resp.reasoning_content == "Let me think..."

    def test_usage_dict(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        resp = LLMResponse(content="", tool_calls=[], finish_reason="stop", usage=usage)
        assert resp.usage == usage


class TestToolCall:
    def test_tool_call_creation(self):
        tc = ToolCall(id="call_1", name="search", arguments={"q": "test"})
        assert tc.id == "call_1"
        assert tc.name == "search"
        assert tc.arguments == {"q": "test"}


class TestCreateMessage:
    def test_create_message(self, mock_llm_client):
        msg = mock_llm_client.create_message("user", "hello")
        assert msg == {"role": "user", "content": "hello"}

    def test_create_system_message(self, mock_llm_client):
        msg = mock_llm_client.create_message("system", "prompt")
        assert msg == {"role": "system", "content": "prompt"}


class TestCreateToolMessage:
    def test_create_tool_message(self, mock_llm_client):
        msg = mock_llm_client.create_tool_message("tc_1", "search", {"result": "ok"})
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "tc_1"
        assert msg["name"] == "search"
        assert "ok" in msg["content"]


class TestParseResponse:
    def test_parse_simple_text_response(self):
        response = make_mock_openai_response(content="hello world")
        result = LLMClient._parse_response(response)
        assert result.content == "hello world"
        assert result.finish_reason == "stop"
        assert result.tool_calls == []

    def test_parse_response_with_tool_calls(self):
        response = make_mock_openai_response(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[
                {"id": "call_1", "name": "search", "arguments": '{"q": "test"}'}
            ],
        )
        result = LLMClient._parse_response(response)
        assert result.has_tool_calls()
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"q": "test"}

    def test_parse_response_with_reasoning(self):
        response = make_mock_openai_response(
            content="answer",
            reasoning_content="thinking...",
        )
        result = LLMClient._parse_response(response)
        assert result.reasoning_content == "thinking..."

    def test_parse_response_with_usage(self):
        response = make_mock_openai_response(
            content="test",
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        result = LLMClient._parse_response(response)
        assert result.usage["total_tokens"] == 30


class TestChatWithTools:
    @pytest.mark.asyncio
    async def test_chat_with_tools_returns_llm_response(self, mock_llm_client):
        mock_llm_client.client.chat.completions.create = AsyncMock(
            return_value=make_mock_openai_response(content="test response")
        )
        result = await mock_llm_client.chat_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )
        assert isinstance(result, LLMResponse)
        assert result.content == "test response"

    @pytest.mark.asyncio
    async def test_chat_with_tools_uses_default_temperature(self, mock_llm_client):
        create_mock = AsyncMock(return_value=make_mock_openai_response(content="ok"))
        mock_llm_client.client.chat.completions.create = create_mock
        mock_llm_client.temperature = 0.5

        await mock_llm_client.chat_with_tools(
            messages=[{"role": "user", "content": "hi"}],
        )
        call_args = create_mock.call_args[1]
        assert call_args["temperature"] == 0.5
