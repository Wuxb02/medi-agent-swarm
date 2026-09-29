"""JEV 共用客户端响应与失败边界测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.core.jev_client import JevClient


@pytest.mark.asyncio
async def test_choice_validates_response_and_closes_client():
    client = JevClient("token", "jev-test")
    response = MagicMock()
    response.json.return_value = {
        "answers": {"gate": {"choice": "yes", "confidence": 0.9}}
    }
    transport = MagicMock()
    transport.post = AsyncMock(return_value=response)
    transport.aclose = AsyncMock()
    client._client = transport

    assert await client.choice("问题", "gate", {"type": "choice"},
                               {"yes", "no"}) == ("yes", 0.9)
    transport.post.assert_awaited_once()
    await client.close()
    transport.aclose.assert_awaited_once()
    assert client._client is None


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"choice": "unknown", "confidence": 0.9},
    {"choice": "yes", "confidence": float("nan")},
    {"choice": "yes", "confidence": 1.2},
])
async def test_choice_rejects_invalid_answer(answer):
    client = JevClient("token")
    response = MagicMock()
    response.json.return_value = {"answers": {"gate": answer}}
    client._client = MagicMock(post=AsyncMock(return_value=response))
    with pytest.raises(ValueError):
        await client.choice("问题", "gate", {}, {"yes", "no"})


@pytest.mark.asyncio
async def test_missing_key_and_answers_are_rejected(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        await JevClient(api_key="").ask("问题", {})
    client = JevClient("token")
    response = MagicMock()
    response.json.return_value = {}
    client._client = MagicMock(post=AsyncMock(return_value=response))
    with pytest.raises(ValueError, match="answers"):
        await client.ask("问题", {})
