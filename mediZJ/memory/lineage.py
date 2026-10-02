"""非自进化记忆的来源血缘注册表。"""

from mediZJ.infrastructure.database import transaction

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any


ALLOWED_SOURCE_TYPES = {
    "user_reported",
    "model_inferred",
    "conversation_summary",
    "authoritative_document",
}


class MemoryLineageStore:
    def __init__(self) -> None:
        """存储实例不在构造阶段访问数据库。"""
        self._initialized = True

    def _connect(self):
        return transaction()

    async def record(
        self,
        user_id: str,
        memory_kind: str,
        memory_key: str,
        source_type: str,
        **source: Any,
    ) -> str:
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise ValueError("非法记忆来源类型")
        key_hash = hashlib.sha256(memory_key.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        lineage_id = "lineage_" + uuid.uuid4().hex
        async with self._connect() as conn:
            existing = (
                await conn.execute(
                    """
                SELECT lineage_id FROM memory_lineage
                WHERE owner_user_id = %s AND memory_kind = %s AND memory_key_hash = %s
                """,
                    (user_id, memory_kind, key_hash),
                )
            ).fetchone()
            if existing:
                lineage_id = existing["lineage_id"]
            (
                await conn.execute(
                    """
                INSERT INTO memory_lineage (
                    lineage_id, owner_user_id, memory_kind, memory_key_hash,
                    source_type, source_message_id, source_trace_id,
                    source_document_id, source_version_id, source_chunk_uid,
                    valid_until, lineage_status, invalidated_reason,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'valid', NULL, %s, %s)
                ON DUPLICATE KEY UPDATE source_type = VALUES(source_type),
                    source_message_id = VALUES(source_message_id),
                    source_trace_id = VALUES(source_trace_id),
                    source_document_id = VALUES(source_document_id),
                    source_version_id = VALUES(source_version_id),
                    source_chunk_uid = VALUES(source_chunk_uid),
                    valid_until = VALUES(valid_until),
                    lineage_status = 'valid', invalidated_reason = NULL,
                    updated_at = VALUES(updated_at)
                """,
                    (
                        lineage_id,
                        user_id,
                        memory_kind,
                        key_hash,
                        source_type,
                        source.get("source_message_id"),
                        source.get("source_trace_id"),
                        source.get("source_document_id"),
                        source.get("source_version_id"),
                        source.get("source_chunk_uid"),
                        source.get("valid_until"),
                        now,
                        now,
                    ),
                )
            )
        return lineage_id

    async def is_valid(self, user_id: str, memory_kind: str, memory_key: str) -> bool:
        key_hash = hashlib.sha256(memory_key.encode("utf-8")).hexdigest()
        async with self._connect() as conn:
            row = (
                await conn.execute(
                    """
                SELECT lineage_status, valid_until FROM memory_lineage
                WHERE owner_user_id = %s AND memory_kind = %s AND memory_key_hash = %s
                """,
                    (user_id, memory_kind, key_hash),
                )
            ).fetchone()
        if not row:
            return True
        if row["lineage_status"] != "valid":
            return False
        if not row["valid_until"]:
            return True
        try:
            valid_until = datetime.fromisoformat(
                row["valid_until"].replace("Z", "+00:00")
            )
        except ValueError:
            return False
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        return valid_until > datetime.now(timezone.utc)

    async def invalidate_document(self, document_id: str, reason: str) -> int:
        async with self._connect() as conn:
            cursor = await conn.execute(
                """
                UPDATE memory_lineage
                SET lineage_status = 'stale', invalidated_reason = %s, updated_at = %s
                WHERE source_document_id = %s AND lineage_status = 'valid'
                """,
                (reason, datetime.now(timezone.utc).isoformat(), document_id),
            )
            return cursor.rowcount

    async def delete_user(self, user_id: str) -> int:
        async with self._connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM memory_lineage WHERE owner_user_id = %s", (user_id,)
            )
            return cursor.rowcount
