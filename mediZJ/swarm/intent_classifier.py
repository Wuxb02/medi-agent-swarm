"""意图识别：判断用户输入是否涉及医疗/健康诉求，用于门控长期记忆检索。"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict

from mediZJ.core.llm_client import LLMClient
from mediZJ.core.prompt_loader import PromptLoader

# 合法意图取值
_MEDICAL = "medical"
_OTHERS = "others"
_VALID_INTENTS = frozenset({_MEDICAL, _OTHERS})


@dataclass
class IntentResult:
    """意图识别结果。"""

    intent: str            # medical | others
    confidence: float      # 0.0 ~ 1.0
    source: str            # "llm" | "fallback"
    reason: str = ""

    @property
    def skip_long_term(self) -> bool:
        """是否跳过 Mem0 长期记忆检索（仅非医疗输入跳过）。"""
        return self.intent == _OTHERS


class IntentClassifier:
    """基于 LLM 的意图识别器（纯 LLM 判断，无规则短路层）。

    医学安全优先：判断失败或不确定时一律降级为 medical（不跳过检索），
    宁可多检索一次 Mem0，也不丢医疗问题。
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        timeout: float = 3.0,
    ) -> None:
        self.llm_client = llm_client or LLMClient()
        self.timeout = timeout

    async def classify(self, question: str) -> IntentResult:
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
                            "content": "你是医疗助手的意图识别模块，仅输出 JSON。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    response_format={"type": "json_object"},
                ),
                timeout=self.timeout,
            )
            return self._normalize(json.loads(raw))
        except (asyncio.TimeoutError, json.JSONDecodeError, KeyError) as exc:
            return self._fallback(reason=f"意图识别失败: {exc}")
        except Exception as exc:
            return self._fallback(reason=f"意图识别异常: {exc}")

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
