"""意图识别：判断用户输入是否涉及医疗/健康诉求。"""

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict

import httpx
from loguru import logger

from mediZJ.core.llm_client import LLMClient
from mediZJ.core.prompt_loader import PromptLoader
from mediZJ.memory.prompt_prefix import PromptPrefixAssembler

# 合法意图取值
_MEDICAL = "medical"
_OTHERS = "others"
_VALID_INTENTS = frozenset({_MEDICAL, _OTHERS})
_JEV_QUESTION = {
    "intent": {
        "type": "choice",
        "instructions": (
            "用户输入是否包含医疗或健康诉求？只要可能涉及健康，"
            "包括寒暄后提出症状，应选择 medical。"
        ),
        "criteria": {
            "medical": (
                "症状、疾病、用药、检查报告、健康生活方式等咨询；"
                "寒暄与医疗诉求混合，或含糊但可能涉及健康。"
            ),
            "others": "完全不涉及健康的寒暄、致谢、道别、系统能力询问或其他话题。",
        },
    }
}


class JevIntentClient:
    """TypeSafe 的意图识别 HTTP 客户端。"""

    def __init__(self, api_key: str, model: str = "jev-1.13.0") -> None:
        self.api_key = api_key
        self.model = model
        self._client: httpx.AsyncClient | None = None

    async def classify(self, question: str, timeout: float) -> dict[str, Any]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=timeout)
        response = await self._client.post(
            "https://api.typesafe.ai/v1/systemone",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "state": question,
                "model": self.model,
                "questions": _JEV_QUESTION,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


@dataclass
class IntentResult:
    """意图识别结果。"""

    intent: str            # medical | others
    confidence: float      # 0.0 ~ 1.0
    source: str            # "llm" | "jev" | "fallback"
    reason: str = ""

    @property
    def skip_long_term(self) -> bool:
        """是否跳过医疗情景记忆检索。"""
        return self.intent == _OTHERS


class IntentClassifier:
    """默认使用 JEV 的意图识别器，也支持 LLM 与影子对照。

    医学安全优先：判断失败或不确定时一律降级为 medical（不跳过检索），
    宁可多执行一次医疗路由，也不丢失医疗问题。
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        timeout: float = 3.0,
        mode: str | None = None,
        jev_client: JevIntentClient | None = None,
        others_threshold: float | None = None,
        shadow_allowed: bool = False,
        on_observation: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.mode = mode or os.getenv("INTENT_CLASSIFIER_MODE", "jev")
        if self.mode not in {"llm", "shadow", "jev"}:
            raise ValueError("INTENT_CLASSIFIER_MODE 必须为 llm、shadow 或 jev")
        self.llm_client = llm_client or LLMClient()
        self.timeout = timeout
        self.others_threshold = (
            float(os.getenv("JEV_OTHERS_THRESHOLD", "0.9"))
            if others_threshold is None else others_threshold
        )
        if not 0 <= self.others_threshold <= 1:
            raise ValueError("JEV_OTHERS_THRESHOLD 必须位于 [0, 1]")
        self.jev_client = jev_client
        self.shadow_allowed = shadow_allowed
        self.on_observation = on_observation or self._log_observation

    def _get_jev_client(self) -> JevIntentClient:
        if self.jev_client is None:
            api_key = os.getenv("TYPESAFE_API_KEY")
            if not api_key:
                raise ValueError("TYPESAFE_API_KEY 未配置")
            self.jev_client = JevIntentClient(
                api_key, os.getenv("JEV_MODEL", "jev-1.13.0")
            )
        return self.jev_client

    async def classify(self, question: str) -> IntentResult:
        if self.mode == "llm":
            return await self.classify_llm(question)
        if self.mode == "jev":
            return await self.classify_jev(question)

        baseline = await self.classify_llm(question)
        if not self.shadow_allowed:
            return baseline
        started = time.perf_counter()
        candidate = await self.classify_jev_raw(question)
        gated_intent = (
            _MEDICAL
            if candidate.intent == _OTHERS
            and candidate.confidence < self.others_threshold
            else candidate.intent
        )
        self.on_observation({
            "mode": "shadow",
            "baseline_intent": baseline.intent,
            "baseline_confidence": baseline.confidence,
            "jev_intent": candidate.intent,
            "jev_gated_intent": gated_intent,
            "jev_confidence": candidate.confidence,
            "actual_route": baseline.intent,
            "jev_latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "jev_error_type": candidate.reason if candidate.source == "fallback" else None,
            "jev_model": getattr(self.jev_client, "model", None),
        })
        return baseline

    async def classify_llm(self, question: str) -> IntentResult:
        """对用户输入进行意图识别。

        Args:
            question: 用户原始输入。

        Returns:
            IntentResult：intent 为 medical 或 others。
            任何异常（超时、JSON 解析失败、网络错误）均降级为 medical。
        """
        try:
            prompt = PromptLoader.render("memory/intent_gate.j2", question=question)
            raw = await asyncio.wait_for(
                self.llm_client.chat(
                    [
                        {
                            "role": "system",
                            "content": PromptPrefixAssembler.global_prefix(
                                "你是医疗助手的意图识别模块，仅输出 JSON。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    response_format={"type": "json_object"},
                ),
                timeout=self.timeout,
            )
            return self._normalize(json.loads(raw))
        except Exception as exc:
            return self._fallback(reason=type(exc).__name__)

    async def classify_jev(self, question: str) -> IntentResult:
        result = await self.classify_jev_raw(question)
        if result.intent == _OTHERS and result.confidence < self.others_threshold:
            return IntentResult(_MEDICAL, result.confidence, "jev", "低置信度")
        return result

    async def classify_jev_raw(self, question: str) -> IntentResult:
        try:
            response = await asyncio.wait_for(
                self._get_jev_client().classify(question, self.timeout),
                timeout=self.timeout,
            )
            answer = response["answers"]["intent"]
            intent = answer["choice"]
            confidence = float(answer["confidence"])
            if intent not in _VALID_INTENTS or not math.isfinite(confidence):
                raise ValueError("JEV 分类结果无效")
            if not 0 <= confidence <= 1:
                raise ValueError("JEV 置信度无效")
            return IntentResult(intent, confidence, "jev")
        except Exception as exc:
            return self._fallback(type(exc).__name__)

    @staticmethod
    def _normalize(raw: Dict[str, Any]) -> IntentResult:
        """校验并归一化 LLM 输出；未知意图/缺字段时保守兜底为 medical。"""
        intent = str(raw.get("intent", ""))
        if intent not in _VALID_INTENTS:
            return IntentResult(
                intent=_MEDICAL,
                confidence=0.0,
                source="fallback",
                reason=f"未知意图值: {intent!r}",
            )
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return IntentResult(
            intent=intent,
            confidence=confidence,
            source="llm",
            reason=str(raw.get("reason", "")).strip(),
        )

    @staticmethod
    def _fallback(reason: str) -> IntentResult:
        return IntentResult(
            intent=_MEDICAL,
            confidence=0.0,
            source="fallback",
            reason=reason,
        )

    @staticmethod
    def _log_observation(observation: dict[str, Any]) -> None:
        logger.info("Intent shadow observation: {}", observation)
