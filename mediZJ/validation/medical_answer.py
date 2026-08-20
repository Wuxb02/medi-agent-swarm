"""最终回答的引用与医疗安全校验。"""

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from loguru import logger

from mediZJ.core.llm_client import LLMClient
from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase


_HIGH_RISK = (
    "胸痛", "呼吸困难", "昏厥", "剧烈头痛", "意识不清", "抽搐",
    "突然无力", "大量出血", "自杀", "轻生",
)
_CARE_TERMS = ("就医", "急诊", "医院", "120", "医生")
_DIAGNOSIS_PATTERNS = (
    r"您(?:就是|肯定是|已经|患有)",
    r"可以确诊为",
    r"这就是\S{1,20}(?:病|症)",
)
_PRESCRIPTION_PATTERN = re.compile(
    r"(?:建议|应当|需要)(?:服用|使用|注射).{0,20}"
    r"\d+(?:\.\d+)?\s*(?:mg|g|毫克|克|ml|毫升)",
    re.IGNORECASE,
)
_NUMERIC_MEDICAL = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mmHg|mmol/L|mg/dL|mg|g|毫克|克|毫升|%)",
    re.IGNORECASE,
)


@dataclass
class VerificationResult:
    passed: bool
    dimension_scores: dict[str, float]
    violations: list[str]
    rewrite_instructions: list[str]
    validated_citations: list[dict[str, Any]]
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CitationValidator:
    """将输出引用重新绑定到 active 文档版本。"""

    def __init__(
        self,
        catalog: Optional[KnowledgeCatalog] = None,
        knowledge_base: Optional[MedicalKnowledgeBase] = None,
    ) -> None:
        self.catalog = catalog or KnowledgeCatalog()
        self.knowledge_base = knowledge_base

    def validate(self, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for citation in citations:
            item = dict(citation)
            document_id = str(
                item.get("document_id") or item.get("doc_id") or ""
            )
            version_id = str(item.get("version_id") or "")
            version = (
                self.catalog.active_by_version(version_id)
                if version_id
                else self.catalog.active_version(document_id)
            )
            if not version:
                continue
            if document_id and version["document_id"] != document_id:
                continue
            chunk_uid = str(item.get("chunk_uid") or "")
            if chunk_uid:
                if self.knowledge_base is None:
                    self.knowledge_base = MedicalKnowledgeBase()
                chunks = self.knowledge_base.get_document_chunks(version["version_id"])
                if not any(
                    chunk.get("metadata", {}).get("chunk_uid") == chunk_uid
                    for chunk in chunks
                ):
                    continue
            item.update(
                {
                    "doc_id": version["document_id"],
                    "document_id": version["document_id"],
                    "version_id": version["version_id"],
                    "document_version": str(version["version"]),
                    "effective_at": version["effective_at"],
                    "authority_level": version["authority_level"],
                    "validation_status": "valid",
                }
            )
            item["conflicts"] = self.catalog.list_conflicts(
                "pending", [version["version_id"]]
            ) + self.catalog.list_conflicts(
                "confirmed", [version["version_id"]]
            )
            valid.append(item)
        for index, item in enumerate(valid, 1):
            item["index"] = index
        return valid


class MedicalAnswerVerifier:
    """确定性规则优先，LLM 仅进行语义补充校验。"""

    SAFE_FALLBACK = (
        "当前回答未能通过医疗安全校验，不建议据此自行诊断或用药。"
        "如果症状明显、持续加重，或出现胸痛、呼吸困难、昏厥、"
        "意识异常等情况，请立即就医或拨打 120。"
    )

    def __init__(
        self,
        citation_validator: Optional[CitationValidator] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.citation_validator = citation_validator or CitationValidator()
        self.llm_client = llm_client

    async def verify(
        self,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
    ) -> VerificationResult:
        validated = self.citation_validator.validate(citations)
        violations = self._deterministic_violations(
            question, answer, citations, validated
        )
        semantic = await self._semantic_verify(question, answer, validated)
        violations.extend(
            str(item) for item in semantic.get("violations", [])
            if str(item) not in violations
        )
        passed = not violations and bool(semantic.get("passed", True))
        scores = {
            "medical_safety": 5.0 if not violations else 2.0,
            "accuracy_evidence": 5.0 if validated or not citations else 2.0,
            "completeness": float(semantic.get("completeness", 4.0)),
            "tool_use": 4.0,
            "routing": 4.0,
            "personalization": float(semantic.get("personalization", 4.0)),
            "clarity": float(semantic.get("clarity", 4.0)),
        }
        return VerificationResult(
            passed=passed,
            dimension_scores=scores,
            violations=violations,
            rewrite_instructions=[f"修正：{item}" for item in violations],
            validated_citations=validated,
        )

    async def rewrite_once(
        self,
        question: str,
        answer: str,
        result: VerificationResult,
    ) -> str:
        if self.llm_client is None:
            self.llm_client = LLMClient()
        evidence = "\n".join(
            str(item.get("content") or item.get("snippet") or "")[:800]
            for item in result.validated_citations
        )
        prompt = (
            "请仅基于有效证据重写医疗助手回答。不得确诊，不得开具具体处方，"
            "遇到高风险信号必须建议立即就医。\n"
            f"问题：{question}\n原回答：{answer}\n"
            f"必须修正：{'; '.join(result.violations)}\n有效证据：{evidence}"
        )
        return await self.llm_client.chat(
            [
                {"role": "system", "content": "你是保守的医疗安全编辑。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )

    async def verify_and_rewrite(
        self,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
    ) -> tuple[str, VerificationResult]:
        first = await self.verify(question, answer, citations)
        if first.passed:
            return answer, first
        try:
            rewritten = await self.rewrite_once(question, answer, first)
            second = await self.verify(
                question, rewritten, first.validated_citations
            )
        except Exception as exc:
            logger.warning("医疗安全重写失败: {}", exc)
            first.degraded = True
            return self.SAFE_FALLBACK, first
        if second.passed:
            return rewritten, second
        second.degraded = True
        return self.SAFE_FALLBACK, second

    @staticmethod
    def _deterministic_violations(
        question: str,
        answer: str,
        original_citations: list[dict[str, Any]],
        validated_citations: list[dict[str, Any]],
    ) -> list[str]:
        violations = []
        combined = question + "\n" + answer
        if any(term in combined for term in _HIGH_RISK) and not any(
            term in answer for term in _CARE_TERMS
        ):
            violations.append("高风险症状未明确建议就医")
        if any(re.search(pattern, answer) for pattern in _DIAGNOSIS_PATTERNS):
            violations.append("存在越界的确定性诊断")
        if _PRESCRIPTION_PATTERN.search(answer):
            violations.append("存在未经医生评估的具体处方建议")
        if original_citations and len(validated_citations) < len(original_citations):
            violations.append("存在失效或无法核验的引用")
        if _NUMERIC_MEDICAL.search(answer) and not validated_citations:
            violations.append("数值性医学主张缺少有效来源")
        if any(item.get("conflicts") for item in validated_citations) and not any(
            term in answer for term in ("冲突", "差异", "适用人群", "专业医生")
        ):
            violations.append("未说明引用来源之间尚存未解决差异")
        return violations

    async def _semantic_verify(
        self,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.llm_client is None:
            return {"passed": True}
        evidence = "\n".join(
            str(item.get("content") or item.get("snippet") or "")[:600]
            for item in citations
        )
        prompt = (
            "仅输出 JSON："
            '{"passed":true,"violations":[],"completeness":4,'
            '"personalization":4,"clarity":4}。'
            "检查回答是否与证据矛盾、遗漏危险信号或把用户自述当成确诊。\n"
            f"问题：{question}\n回答：{answer}\n证据：{evidence}"
        )
        try:
            raw = await self.llm_client.chat(
                [
                    {"role": "system", "content": "你是医疗安全校验器。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"passed": False}
        except Exception as exc:
            logger.warning("语义安全校验失败: {}", exc)
            return {"passed": False, "violations": ["语义安全校验未完成"]}
