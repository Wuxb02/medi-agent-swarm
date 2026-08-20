"""非自进化记忆的来源血缘注册表。"""

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mediZJ.memory.session_db import _DEFAULT_DB_PATH


ALLOWED_SOURCE_TYPES = {
    "user_reported",
    "model_inferred",
    "conversation_summary",
    "authoritative_document",
}


class MemoryLineageStore:
    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_lineage (
                    lineage_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    memory_kind TEXT NOT NULL,
                    memory_key_hash TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_message_id TEXT,
                    source_trace_id TEXT,
                    source_document_id TEXT,
                    source_version_id TEXT,
                    source_chunk_uid TEXT,
                    valid_until TEXT,
                    lineage_status TEXT NOT NULL DEFAULT 'valid',
                    invalidated_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, memory_kind, memory_key_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_lineage_document
                    ON memory_lineage(source_document_id, lineage_status);
                """
            )

    def record(
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
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT lineage_id FROM memory_lineage
                WHERE owner_user_id = ? AND memory_kind = ? AND memory_key_hash = ?
                """,
                (user_id, memory_kind, key_hash),
            ).fetchone()
            if existing:
                lineage_id = existing["lineage_id"]
            conn.execute(
                """
                INSERT INTO memory_lineage (
                    lineage_id, owner_user_id, memory_kind, memory_key_hash,
                    source_type, source_message_id, source_trace_id,
                    source_document_id, source_version_id, source_chunk_uid,
                    valid_until, lineage_status, invalidated_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'valid', NULL, ?, ?)
                ON CONFLICT(owner_user_id, memory_kind, memory_key_hash)
                DO UPDATE SET source_type = excluded.source_type,
                    source_message_id = excluded.source_message_id,
                    source_trace_id = excluded.source_trace_id,
                    source_document_id = excluded.source_document_id,
                    source_version_id = excluded.source_version_id,
                    source_chunk_uid = excluded.source_chunk_uid,
                    valid_until = excluded.valid_until,
                    lineage_status = 'valid', invalidated_reason = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    lineage_id, user_id, memory_kind, key_hash, source_type,
                    source.get("source_message_id"), source.get("source_trace_id"),
                    source.get("source_document_id"), source.get("source_version_id"),
                    source.get("source_chunk_uid"), source.get("valid_until"), now, now,
                ),
            )
        return lineage_id

    def is_valid(self, user_id: str, memory_kind: str, memory_key: str) -> bool:
        key_hash = hashlib.sha256(memory_key.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT lineage_status, valid_until FROM memory_lineage
                WHERE owner_user_id = ? AND memory_kind = ? AND memory_key_hash = ?
                """,
                (user_id, memory_kind, key_hash),
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

    def invalidate_document(self, document_id: str, reason: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE memory_lineage
                SET lineage_status = 'stale', invalidated_reason = ?, updated_at = ?
                WHERE source_document_id = ? AND lineage_status = 'valid'
                """,
                (reason, datetime.now(timezone.utc).isoformat(), document_id),
            )
            return cursor.rowcount

    def delete_user(self, user_id: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM memory_lineage WHERE owner_user_id = ?", (user_id,)
            )
            return cursor.rowcount
