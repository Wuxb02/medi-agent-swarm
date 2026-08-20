"""医学知识冲突候选检测。"""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from mediZJ.core.llm_client import LLMClient
from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase


_NUMBER = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mmHg|mmol/L|mg/dL|mg|g|毫克|克|%|岁)",
    re.IGNORECASE,
)
_CONTRAINDICATIONS = ("禁忌", "不得", "不宜", "慎用", "避免", "可以", "推荐")


class MedicalConflictDetector:
    def __init__(
        self,
        catalog: Optional[KnowledgeCatalog] = None,
        knowledge_base: Optional[MedicalKnowledgeBase] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.catalog = catalog or KnowledgeCatalog()
        self.knowledge_base = knowledge_base or MedicalKnowledgeBase()
        self.llm_client = llm_client

    async def detect_version(self, version_id: str) -> list[dict[str, Any]]:
        version = self.catalog.get_version(version_id)
        if not version:
            raise LookupError("文档版本不存在")
        conflicts: list[dict[str, Any]] = []
        try:
            chunks = self.knowledge_base.get_document_chunks(version_id)
            for chunk in chunks:
                text = chunk["content"]
                if not self._has_conflict_signal(text):
                    continue
                candidates = self.knowledge_base.search(text, top_k=12)
                for candidate in candidates:
                    metadata = candidate.get("metadata", {})
                    other_version_id = metadata.get("version_id") or metadata.get(
                        "physical_doc_id", ""
                    )
                    if not other_version_id or other_version_id == version_id:
                        continue
                    other = self.catalog.active_by_version(other_version_id)
                    if not other or not self._same_scope(version, other):
                        continue
                    other_chunks = self.knowledge_base.get_document_chunks(
                        other_version_id
                    )
                    for other_chunk in other_chunks:
                        if not self._potential_conflict(text, other_chunk["content"]):
                            continue
                        verdict = await self._judge(text, other_chunk["content"])
                        if not verdict.get("is_conflict"):
                            continue
                        conflict = {
                            "conflict_id": "conflict_" + uuid.uuid4().hex,
                            "left_version_id": version_id,
                            "left_chunk_uid": chunk["metadata"]["chunk_uid"],
                            "right_version_id": other_version_id,
                            "right_chunk_uid": other_chunk["metadata"]["chunk_uid"],
                            "conflict_type": verdict.get("conflict_type", "guideline_difference"),
                            "similarity_score": candidate.get("score", 0),
                            "confidence": verdict.get("confidence", 0.6),
                            "explanation": verdict.get("explanation", "疑似医学结论不一致"),
                            "detection_status": "completed",
                            "review_status": "pending",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        }
                        self.catalog.upsert_conflict(conflict)
                        conflicts.append(conflict)
            return conflicts
        except Exception as exc:
            logger.exception("知识冲突检测失败: {}", version_id)
            failure = {
                "conflict_id": "conflict_" + uuid.uuid4().hex,
                "left_version_id": version_id,
                "left_chunk_uid": f"{version_id}:detection",
                "right_version_id": version_id,
                "right_chunk_uid": f"{version_id}:failure",
                "conflict_type": "detection_failure",
                "detection_status": "failed",
                "review_status": "pending",
                "error": str(exc)[:1000],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.catalog.upsert_conflict(failure)
            return [failure]

    @staticmethod
    def _same_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
        same_disease = bool(left["disease"] and left["disease"] == right["disease"])
        return same_disease or left["doc_type"] == right["doc_type"]

    @staticmethod
    def _has_conflict_signal(text: str) -> bool:
        return bool(_NUMBER.search(text)) or any(term in text for term in _CONTRAINDICATIONS)

    @staticmethod
    def _potential_conflict(left: str, right: str) -> bool:
        left_numbers = set(_NUMBER.findall(left))
        right_numbers = set(_NUMBER.findall(right))
        numeric_difference = bool(left_numbers and right_numbers and left_numbers != right_numbers)
        opposing_terms = (
            any(term in left for term in ("禁忌", "不得", "不宜"))
            != any(term in right for term in ("禁忌", "不得", "不宜"))
        )
        return numeric_difference or opposing_terms

    async def _judge(self, left: str, right: str) -> dict[str, Any]:
        if self.llm_client is None:
            return {
                "is_conflict": True,
                "conflict_type": "threshold_or_contraindication",
                "confidence": 0.6,
                "explanation": "数值阈值或禁忌表述不一致，需人工复核",
            }
        prompt = (
            "判断两段医学文本是否针对相同人群和场景存在矛盾。"
            "仅输出 JSON，字段为 is_conflict、conflict_type、confidence、explanation。\n"
            f"文本A：{left[:1000]}\n文本B：{right[:1000]}"
        )
        raw = await self.llm_client.chat(
            [
                {"role": "system", "content": "你只识别疑似冲突，不裁决医学事实。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"is_conflict": False}
