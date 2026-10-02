"""验证单层重试、熔断及流式调用期间的容量租约。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.core import llm_client as module
from tests.helpers import make_mock_openai_response


@pytest.fixture
def client(monkeypatch):
    sdk = MagicMock()
    monkeypatch.setattr(module, "_get_shared_openai_client", lambda *args: sdk)
    monkeypatch.setattr(module, "_breaker", MagicMock(is_open=False))

    @asynccontextmanager
    async def capacity():
        yield

    monkeypatch.setattr(module, "llm_capacity", capacity)
    return module.LLMClient(
        api_key="test", base_url="https://example.invalid", model_name="test"
    )


async def test_chat_sanitizes_content_and_preserves_usage(client):
    response = make_mock_openai_response(
        content='回答<invoke name="search">隐藏</invoke>', usage={"prompt_tokens": 10}
    )
    response.usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        prompt_tokens_details=SimpleNamespace(cached_tokens=2),
    )
    client.client.chat.completions.create = AsyncMock(return_value=response)
    result = await client.chat_with_tools(
        [{"role": "user", "content": "问题"}],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        tool_choice="required",
    )
    assert result.content == "回答"
    assert result.usage["cached_prompt_tokens"] == 2
    assert (
        client.client.chat.completions.create.await_args.kwargs["tool_choice"]
        == "required"
    )
    assert await client.chat([]) == "回答"
    module._breaker.record_success.assert_called()


@pytest.mark.parametrize("method", ["chat_with_retry", "chat_with_tools_retry"])
@pytest.mark.parametrize(
    "error,attempts", [("connection reset", 3), ("401 unauthorized", 1)]
)
async def test_only_transient_errors_retry_and_attempts_are_capped(
    client, monkeypatch, method, error, attempts
):
    call = AsyncMock(side_effect=RuntimeError(error))
    monkeypatch.setattr(
        client, "chat" if method == "chat_with_retry" else "chat_with_tools", call
    )
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    with pytest.raises(RuntimeError, match=error):
        await getattr(client, method)([], max_retries=10)
    assert call.await_count == attempts
    assert module.asyncio.sleep.await_count == attempts - 1


@pytest.mark.parametrize("stream", [False, True])
async def test_open_breaker_never_calls_model_or_counts_failure(client, stream):
    module._breaker.is_open = True
    client.client.chat.completions.create = AsyncMock()
    with pytest.raises(module.CircuitBreakerOpenError):
        await (
            client.chat_with_tools_stream([]) if stream else client.chat_with_tools([])
        )
    client.client.chat.completions.create.assert_not_awaited()
    module._breaker.record_failure.assert_not_called()


async def test_stream_holds_slot_and_assembles_fragmented_tools(client, monkeypatch):
    held = []

    @asynccontextmanager
    async def capacity():
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(module, "llm_capacity", capacity)

    def chunk(content=None, reasoning=None, tools=None, finish=None):
        delta = SimpleNamespace(
            content=content, reasoning_content=reasoning, tool_calls=tools
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None
        )

    def tool(index, identifier=None, name=None, arguments=None):
        return SimpleNamespace(
            index=index,
            id=identifier,
            function=SimpleNamespace(name=name, arguments=arguments),
        )

    values = [
        chunk(content="建议", reasoning="核验"),
        chunk(content='<invoke name="search">'),
        chunk(content="内部"),
        chunk(content="</invoke>"),
        chunk(content="复诊"),
        chunk(tools=[tool(0, "call", "search", '{"q":')]),
        chunk(
            tools=[tool(0, arguments='"test"}'), tool(1, "bad", "search", "invalid")],
            finish="tool_calls",
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            ),
        ),
    ]

    async def chunks():
        for value in values:
            assert held == [True]
            yield value

    client.client.chat.completions.create = AsyncMock(return_value=chunks())
    content, reasoning = [], []
    detected = MagicMock()
    result = await client.chat_with_tools_stream(
        [],
        on_content_token=content.append,
        on_reasoning_token=reasoning.append,
        on_tools_detected=detected,
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        tool_choice="required",
    )
    assert result.content == "建议复诊"
    assert content == ["建议", "复诊"] and reasoning == ["核验"]
    assert result.tool_calls[0].arguments == {"q": "test"}
    assert result.tool_calls[1].arguments == {}
    assert result.usage["total_tokens"] == 15
    assert held == []
    detected.assert_called_once()
    module._breaker.record_success.assert_called_once()


async def test_stream_failure_records_breaker_failure(client):
    client.client.chat.completions.create = AsyncMock(
        side_effect=ConnectionError("断线")
    )
    with pytest.raises(ConnectionError):
        await client.chat_with_tools_stream([])
    module._breaker.record_failure.assert_called_once()
