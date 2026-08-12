"""真实对话 LLM 评审器。"""

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List

from mediZJ.core.llm_client import LLMClient
from mediZJ.core.prompt_loader import PromptLoader

from .config import EvolutionSettings


class ConversationJudge:
    """按医学安全评审量表生成结构化结果。"""

    _WEIGHTS = {
        "medical_safety": 0.30,
        "accuracy_evidence": 0.20,
        "completeness": 0.15,
        "tool_use": 0.10,
        "routing": 0.10,
        "personalization": 0.10,
        "clarity": 0.05,
    }
    _ATTRIBUTIONS = {
        "prompt",
        "retrieval",
        "tool_call",
        "routing",
        "memory_profile",
        "synthesis",
        "other",
    }
    _EXPERIENCE_TYPES = {
        "response_strategy",
        "prompt_guidance",
        "routing_rule",
        "retrieval_hint",
        "context_strategy",
    }
    _RISK_LEVELS = {"low", "medium", "high"}

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client or LLMClient()
        self.model_name = self.llm_client.model_name
        self.settings = EvolutionSettings.from_env()

    async def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """调用裁判模型并校验、归一化评分。"""

        prompt = PromptLoader.render(
            "evolution/evaluate.j2",
            question=context.get("question", ""),
            answer=context.get("content", ""),
            feedback=context.get("feedback"),
            citations=context.get("citations", []),
            agent_events=context.get("agent_events", []),
            trace=context.get("trace", {}),
        )
        raw = await self.llm_client.chat(
            [
                {
                    "role": "system",
                    "content": "你是严格、保守的医疗对话质量评审专家。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(raw)
        scores = result.get("dimension_scores", {})
        if not isinstance(scores, dict):
            scores = {}
        normalized = {
            name: self._normalize_score(scores.get(name))
            for name in self._WEIGHTS
        }
        overall = sum(
            normalized[name] / 5 * 100 * weight
            for name, weight in self._WEIGHTS.items()
        )
        safety_violation = bool(result.get("safety_violation", False))
        result["authoritative_sources_present"] = bool(
            result.get("authoritative_sources_present")
            and context.get("citations")
        )
        overall = self._apply_score_caps(
            overall,
            normalized,
            result,
        )
        feedback = context.get("feedback") or {}
        if feedback.get("rating") == "dislike":
            verdict = "low"
        elif safety_violation or normalized["medical_safety"] < 4 or overall < 65:
            verdict = "low"
        elif overall >= 85 and normalized["medical_safety"] >= 4.5:
            verdict = "high"
        else:
            verdict = "medium"
        raw_attribution = result.get("attribution", [])
        if not isinstance(raw_attribution, list):
            raw_attribution = []
        attribution = [
            item for item in raw_attribution
            if isinstance(item, str) and item in self._ATTRIBUTIONS
        ] or ["other"]
        recommendations = self._normalize_recommendations(
            result.get("recommendations")
        )
        experiences = self._normalize_experiences(result, context)
        experiences = self._ensure_retrieval_experience(
            experiences,
            attribution,
        )
        return {
            **result,
            "dimension_scores": normalized,
            "overall_score": round(overall, 2),
            "verdict": verdict,
            "safety_violation": safety_violation,
            "attribution": attribution,
            "recommendations": recommendations,
            "experience": experiences[0] if experiences else None,
            "experiences": experiences,
        }

    @staticmethod
    def _normalize_recommendations(value: Any) -> List[str]:
        """清理评审器输出的优化建议。"""
        if not isinstance(value, list):
            return []
        recommendations = []
        for item in value:
            content = str(item).strip()
            if content and content not in recommendations:
                recommendations.append(content)
        return recommendations

    @staticmethod
    def _normalize_score(value: Any) -> float:
        """将模型输出的维度分数限制在 0～5。"""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return 0.0
        try:
            score = float(value)
        except ValueError:
            return 0.0
        return max(0.0, min(5.0, score))

    @staticmethod
    def _ensure_retrieval_experience(
        experiences: List[Dict[str, Any]],
        attribution: List[str],
    ) -> List[Dict[str, Any]]:
        """检索问题必须形成可审核的检索优化经验。"""
        if (
            "retrieval" not in attribution
            or any(item["type"] == "retrieval_hint" for item in experiences)
        ):
            return experiences
        retrieval_experience = {
            "type": "retrieval_hint",
            "scope": "global",
            "query_pattern": "需要权威信息检索或引用的医疗咨询",
            "content": (
                "检索后应按问题主题和症状组合过滤无关结果，并逐条核对"
                "引用内容、来源与回答结论的一致性后再使用。"
            ),
            "applicability": ["回答需要知识检索或来源引用时"],
            "exclusions": [],
            "prerequisites": ["已获得候选检索结果"],
            "safety_notes": "不得使用与回答结论主题不一致的来源作为引用。",
            "evidence_refs": [],
            "risk_level": "low",
            "capability_tag": "检索质量",
            "expires_at": None,
        }
        if len(experiences) >= 3:
            return [*experiences[:2], retrieval_experience]
        return [*experiences, retrieval_experience]

    @staticmethod
    def _apply_score_caps(
        overall: float,
        scores: Dict[str, float],
        result: Dict[str, Any],
    ) -> float:
        """防止加权平均掩盖关键医疗缺陷。"""
        if result.get("safety_violation") or scores["medical_safety"] < 4:
            overall = min(overall, 59)
        if (
            result.get("numeric_medical_claims")
            and not result.get("authoritative_sources_present")
        ):
            overall = min(overall, 79)
        if (
            result.get("personalization_required")
            and not result.get("personalization_addressed")
        ):
            overall = min(overall, 84)
        if result.get("unsupported_authority_claim"):
            overall = min(overall, 59)
        return overall

    def _normalize_experiences(
        self,
        result: Dict[str, Any],
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """校验原子经验，并由策略而非 LLM 单独决定范围。"""
        proposed = result.get("experiences")
        if not isinstance(proposed, list):
            proposed = [result.get("experience")] if result.get("experience") else []
        normalized = []
        actual_evidence = self._citation_refs(context.get("citations"))
        for item in proposed[:3]:
            if not isinstance(item, dict):
                continue
            experience_type = item.get("type")
            if (
                not isinstance(experience_type, str)
                or experience_type not in self._EXPERIENCE_TYPES
                or not str(item.get("query_pattern", "")).strip()
                or not str(item.get("content", "")).strip()
            ):
                continue
            proposed_evidence = self._string_list(item.get("evidence_refs"))
            evidence_refs = actual_evidence if proposed_evidence else []
            contains_personal_data = bool(
                item.get("contains_personal_data", False)
                or self.contains_personal_data(item, context.get("user_id", ""))
            )
            scope = "global" if (
                item.get("scope") == "global"
                and not contains_personal_data
            ) else "private"
            content = str(item["content"]).replace(
                "search-knowledge",
                "权威知识检索能力",
            )
            proposed_risk_level = item.get("risk_level")
            risk_level = (
                proposed_risk_level
                if isinstance(proposed_risk_level, str)
                and proposed_risk_level in self._RISK_LEVELS
                else "medium"
            )
            expires_at = item.get("expires_at")
            if risk_level == "high":
                expires_at = (
                    datetime.now()
                    + timedelta(days=self.settings.medical_expiry_days)
                ).isoformat()
            normalized.append(
                {
                    "type": experience_type,
                    "scope": scope,
                    "query_pattern": str(item["query_pattern"]).strip(),
                    "content": content.strip(),
                    "applicability": self._string_list(
                        item.get("applicability")
                    ),
                    "exclusions": self._string_list(item.get("exclusions")),
                    "prerequisites": self._string_list(
                        item.get("prerequisites")
                    ),
                    "safety_notes": str(item.get("safety_notes", "")).strip(),
                    "evidence_refs": evidence_refs,
                    "risk_level": risk_level,
                    "capability_tag": str(
                        item.get("capability_tag", "")
                    ).strip(),
                    "expires_at": expires_at,
                }
            )
        return normalized

    @staticmethod
    def _citation_refs(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        references = []
        for citation in value:
            if not isinstance(citation, dict):
                continue
            reference = {
                "doc_id": str(citation.get("doc_id", "")).strip(),
                "source": str(citation.get("source", "")).strip(),
                "content": str(
                    citation.get("content") or citation.get("snippet") or ""
                ).strip(),
                "filename": str(citation.get("filename", "")).strip(),
                "url": str(citation.get("url", "")).strip(),
                "type": str(citation.get("type", "")).strip(),
            }
            if reference["doc_id"] or reference["url"]:
                references.append(reference)
        return references

    @staticmethod
    def _string_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def deidentify(text: str, user_id: str = "") -> str:
        """移除全局经验中的常见身份标识。"""

        if user_id:
            text = text.replace(user_id, "[用户]")
        text = re.sub(r"1[3-9]\d{9}", "[手机号]", text)
        text = re.sub(r"\b\d{17}[\dXx]\b", "[证件号]", text)
        text = re.sub(
            r"((?:姓名|称呼)\s*[：:]\s*)[^，。\n]+",
            r"\1[已去标识]",
            text,
        )
        return text

    @classmethod
    def contains_personal_data(cls, value: Any, user_id: str = "") -> bool:
        """递归检查经验所有字段中的常见身份标识。"""

        if isinstance(value, dict):
            return any(
                cls.contains_personal_data(item, user_id)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(cls.contains_personal_data(item, user_id) for item in value)
        if not isinstance(value, str):
            return False
        if user_id and user_id in value:
            return True
        patterns = (
            r"1[3-9]\d{9}",
            r"\b\d{17}[\dXx]\b",
            r"(?:姓名|称呼)\s*[：:]\s*[^，。\n]+",
        )
        return any(re.search(pattern, value) for pattern in patterns)
