"""对话医学知识候选与可信库内证据审核。"""

import hashlib
import json
import uuid
from typing import Any

from mediZJ.api.services.knowledge_service import search_knowledge, upload_document
from mediZJ.knowledge.catalog import KnowledgeCatalog, _now


class KnowledgeCandidateService:
    """候选与审核记录保存在 Catalog 中，不参与知识检索。"""

    def __init__(self, catalog: KnowledgeCatalog | None = None):
        self.catalog = catalog or KnowledgeCatalog()

    async def trust_source(
        self, document_id: str, source_url: str, actor_id: str
    ) -> dict[str, Any]:
        if not source_url.startswith(("https://", "http://")):
            raise ValueError("需要可核验的来源 URL")
        version = await self.catalog.active_version(document_id)
        if version is None:
            raise LookupError("当前有效文档不存在")
        async with self.catalog._connection() as conn:
            (
                await conn.execute(
                    """INSERT INTO trusted_knowledge_sources
                (version_id, document_id, source_url, verified_by, verified_at)
                VALUES (%s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE version_id = VALUES(version_id), document_id = VALUES(document_id), source_url = VALUES(source_url), verified_by = VALUES(verified_by), verified_at = VALUES(verified_at)""",
                    (version["version_id"], document_id, source_url, actor_id, _now()),
                )
            )
        return {"document_id": document_id, "version_id": version["version_id"]}

    async def trusted_source(self, version_id: str) -> dict[str, Any] | None:
        if (await self.catalog.active_by_version(version_id)) is None:
            return None
        async with self.catalog._connection() as conn:
            row = (
                await conn.execute(
                    "SELECT * FROM trusted_knowledge_sources WHERE version_id = %s",
                    (version_id,),
                )
            ).fetchone()
        return dict(row) if row else None

    async def evidence_hits(self, claim: str) -> list[dict[str, Any]]:
        hits = []
        for item in await search_knowledge(claim, top_k=12):
            metadata = item.metadata
            version_id = metadata.get("version_id", "")
            trusted = await self.trusted_source(version_id)
            if trusted:
                hits.append(
                    {
                        "document_id": trusted["document_id"],
                        "version_id": version_id,
                        "source_url": trusted["source_url"],
                        "excerpt": item.content[:1500],
                    }
                )
        return hits

    async def add_candidate(
        self,
        turn_id: str,
        user_id: str,
        claim: str,
        source_text: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        claim = " ".join(claim.split())
        if not claim or not source_text:
            raise ValueError("知识主张和原文不能为空")
        digest = hashlib.sha256(claim.encode("utf-8")).hexdigest()
        labels = {item["verdict"] for item in evidence}
        status = (
            "conflict"
            if "conflict" in labels
            else "pending_review"
            if "support" in labels
            else "unverified"
        )
        candidate_id = uuid.uuid4().hex
        async with self.catalog._connection() as conn:
            prior = (
                await conn.execute(
                    """SELECT * FROM knowledge_candidates WHERE claim_hash = %s
                AND status != 'rejected' ORDER BY created_at LIMIT 1""",
                    (digest,),
                )
            ).fetchone()
            if prior is not None:
                return self._decode(prior)
            (
                await conn.execute(
                    """INSERT IGNORE INTO knowledge_candidates
                (candidate_id, turn_id, user_id, claim_hash, claim, source_text,
                 evidence_json, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        candidate_id,
                        turn_id,
                        user_id,
                        digest,
                        claim,
                        source_text,
                        json.dumps(evidence, ensure_ascii=False),
                        status,
                        _now(),
                        _now(),
                    ),
                )
            )
            row = (
                await conn.execute(
                    """SELECT * FROM knowledge_candidates
                WHERE turn_id = %s AND claim_hash = %s""",
                    (turn_id, digest),
                )
            ).fetchone()
            if row is None:
                row = (
                    await conn.execute(
                        """SELECT * FROM knowledge_candidates WHERE claim_hash = %s
                    AND status != 'rejected'""",
                        (digest,),
                    )
                ).fetchone()
        return self._decode(row)

    async def list_candidates(self) -> list[dict[str, Any]]:
        async with self.catalog._connection() as conn:
            rows = (
                await conn.execute(
                    "SELECT * FROM knowledge_candidates ORDER BY created_at DESC"
                )
            ).fetchall()
        return [self._decode(row) for row in rows]

    async def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        async with self.catalog._connection() as conn:
            row = (
                await conn.execute(
                    "SELECT * FROM knowledge_candidates WHERE candidate_id = %s",
                    (candidate_id,),
                )
            ).fetchone()
        if row is None:
            raise LookupError("候选不存在")
        return self._decode(row)

    async def update_evidence(
        self, candidate_id: str, evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        candidate = await self.get_candidate(candidate_id)
        if candidate["status"] in {"approved", "rejected"}:
            raise ValueError("已审核候选不能重新核对")
        labels = {item["verdict"] for item in evidence}
        status = (
            "conflict"
            if "conflict" in labels
            else "pending_review"
            if "support" in labels
            else "unverified"
        )
        async with self.catalog._connection() as conn:
            (
                await conn.execute(
                    """UPDATE knowledge_candidates SET evidence_json = %s, status = %s,
                error = NULL, updated_at = %s WHERE candidate_id = %s""",
                    (
                        json.dumps(evidence, ensure_ascii=False),
                        status,
                        _now(),
                        candidate_id,
                    ),
                )
            )
        return await self.get_candidate(candidate_id)

    async def approve(self, candidate_id: str, actor_id: str) -> dict[str, Any]:
        async with self.catalog._connection() as conn:
            await conn.execute(
                "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
            )
            return await self._approve_locked(candidate_id, actor_id)

    async def _approve_locked(self, candidate_id: str, actor_id: str) -> dict[str, Any]:
        candidate = await self.get_candidate(candidate_id)
        if candidate["status"] != "pending_review":
            raise ValueError("候选当前不可批准")
        supports = []
        for item in candidate["evidence"]:
            current = await self.trusted_source(item["version_id"])
            if current and current["source_url"] != item["source_url"]:
                raise ValueError("证据来源已变化，需重新核对")
            if item["verdict"] == "conflict" and current:
                raise ValueError("可信来源存在冲突")
            if item["verdict"] == "support" and current:
                supports.append(item)
        if not supports:
            raise ValueError("缺少当前有效的可信证据")
        try:
            result = await upload_document(
                filename=f"reviewed_{candidate_id}.txt",
                content=candidate["claim"],
                source=supports[0]["source_url"],
            )
        except Exception as exc:
            async with self.catalog._connection() as conn:
                (
                    await conn.execute(
                        """UPDATE knowledge_candidates SET error = %s, updated_at = %s
                    WHERE candidate_id = %s""",
                        (str(exc), _now(), candidate_id),
                    )
                )
            raise
        async with self.catalog._connection() as conn:
            (
                await conn.execute(
                    """UPDATE knowledge_candidates SET status = 'approved',
                document_id = %s, reviewed_by = %s, error = NULL, updated_at = %s
                WHERE candidate_id = %s AND status = 'pending_review'""",
                    (result.doc_id, actor_id, _now(), candidate_id),
                )
            )
        return await self.get_candidate(candidate_id)

    async def reject(self, candidate_id: str, actor_id: str) -> dict[str, Any]:
        candidate = await self.get_candidate(candidate_id)
        if candidate["status"] in {"approved", "rejected"}:
            raise ValueError("候选已完成审核")
        async with self.catalog._connection() as conn:
            (
                await conn.execute(
                    """UPDATE knowledge_candidates SET status = 'rejected',
                reviewed_by = %s, updated_at = %s WHERE candidate_id = %s""",
                    (actor_id, _now(), candidate_id),
                )
            )
        return await self.get_candidate(candidate_id)

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        return item
