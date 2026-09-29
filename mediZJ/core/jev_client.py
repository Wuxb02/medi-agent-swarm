"""可复用的 TypeSafe JEV 问答客户端。"""

import asyncio
import math
import os
from typing import Any

import httpx


class JevClient:
    """统一调用 JEV 并验证选择题响应。"""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY", "")
        self.model = model or os.getenv("JEV_MODEL", "jev-1.13.0")
        self._client: httpx.AsyncClient | None = None

    async def ask(
        self, state: str, questions: dict[str, Any], timeout: float = 3.0
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError("TYPESAFE_API_KEY 未配置")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=timeout)
        response = await asyncio.wait_for(
            self._client.post(
                "https://api.typesafe.ai/v1/systemone",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"state": state, "model": self.model, "questions": questions},
                timeout=timeout,
            ),
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get("answers"), dict):
            raise ValueError("JEV 响应缺少 answers")
        return result

    async def choice(
        self,
        state: str,
        name: str,
        question: dict[str, Any],
        allowed: set[str],
        timeout: float = 3.0,
    ) -> tuple[str, float]:
        result = await self.ask(state, {name: question}, timeout)
        answer = result["answers"][name]
        choice = answer["choice"]
        confidence = float(answer["confidence"])
        if choice not in allowed or not math.isfinite(confidence):
            raise ValueError("JEV 选择结果无效")
        if not 0 <= confidence <= 1:
            raise ValueError("JEV 置信度无效")
        return choice, confidence

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
