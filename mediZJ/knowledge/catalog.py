"""知识文档版本目录。"""

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional


_DEFAULT_DB_PATH = Path(__file__).parent / "data" / "knowledge_catalog.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeCatalog:
    """管理逻辑文档与物理版本，确保激活切换原子化。"""

    _instance: Optional["KnowledgeCatalog"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, db_path: str | Path = _DEFAULT_DB_PATH):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        configured = os.getenv("KNOWLEDGE_CATALOG_DB", "").strip()
        resolved = Path(configured or db_path)
        if (
            getattr(self, "_initialized", False)
            and getattr(self, "db_path", None) == resolved
        ):
            return
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self.db_path: Path = resolved
        self._lock = threading.RLock()
        self._init_schema()
        self._initialized = True

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO knowledge_schema_meta VALUES
                    ('schema_version', '2');

                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    version_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('indexing', 'active', 'archived', 'failed')
                    ),
                    supersedes_version_id TEXT,
                    content_hash TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    doc_type TEXT NOT NULL DEFAULT 'general',
                    disease TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    authority_level TEXT NOT NULL DEFAULT 'user',
                    effective_at TEXT,
                    expires_at TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    UNIQUE(document_id, version)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_active_document
                    ON knowledge_documents(document_id)
                    WHERE status = 'active';
                CREATE INDEX IF NOT EXISTS idx_knowledge_version_status
                    ON knowledge_documents(status, version_id);

                CREATE TABLE IF NOT EXISTS lifecycle_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    target_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_audit (
                    audit_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    target_id TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                DROP TABLE IF EXISTS knowledge_conflicts;
                UPDATE knowledge_schema_meta
                SET value = '2' WHERE key = 'schema_version';
                """
            )

    def begin_version(
        self,
        document_id: str,
        content_hash: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """在串行事务中分配下一版本。"""
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE content_hash = ? AND status != 'failed'
                LIMIT 1
                """,
                (content_hash,),
            ).fetchone()
            if duplicate:
                raise ValueError(f"内容相同的文档已存在: {duplicate['filename']}")
            previous = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE document_id = ? AND status = 'active'
                """,
                (document_id,),
            ).fetchone()
            maximum = conn.execute(
                "SELECT MAX(version) AS value FROM knowledge_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()["value"]
            version = int(maximum or 0) + 1
            version_id = f"kv_{uuid.uuid4().hex}"
            created_at = _now()
            conn.execute(
                """
                INSERT INTO knowledge_documents (
                    version_id, document_id, version, status,
                    supersedes_version_id, content_hash, filename, doc_type,
                    disease, source, authority_level, effective_at, expires_at,
                    created_at
                ) VALUES (?, ?, ?, 'indexing', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    document_id,
                    version,
                    previous["version_id"] if previous else None,
                    content_hash,
                    metadata.get("filename", ""),
                    metadata.get("type", "general"),
                    metadata.get("disease", ""),
                    metadata.get("source", ""),
                    metadata.get("authority_level", "user"),
                    metadata.get("effective_at"),
                    metadata.get("expires_at"),
                    created_at,
                ),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM knowledge_documents WHERE version_id = ?",
                    (version_id,),
                ).fetchone()
            )

    def activate(self, version_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM knowledge_documents WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            if not row:
                raise LookupError("知识文档版本不存在")
            conn.execute(
                """
                UPDATE knowledge_documents SET status = 'archived'
                WHERE document_id = ? AND status = 'active' AND version_id != ?
                """,
                (row["document_id"], version_id),
            )
            conn.execute(
                """
                UPDATE knowledge_documents
                SET status = 'active', activated_at = ?, error = NULL
                WHERE version_id = ?
                """,
                (_now(), version_id),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM knowledge_documents WHERE version_id = ?",
                    (version_id,),
                ).fetchone()
            )

    def mark_failed(self, version_id: str, error: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE knowledge_documents
                SET status = 'failed', error = ? WHERE version_id = ?
                """,
                (error[:1000], version_id),
            )

    def active_version(self, document_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE document_id = ? AND status = 'active'
                """,
                (document_id,),
            ).fetchone()
            return dict(row) if row and self._is_effective(row) else None

    def active_by_version(self, version_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE version_id = ? AND status = 'active'
                """,
                (version_id,),
            ).fetchone()
            return dict(row) if row and self._is_effective(row) else None

    def get_version(self, version_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_documents WHERE version_id = ?",
                (version_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_versions(self, document_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [
                dict(row) for row in conn.execute(
                    """
                    SELECT * FROM knowledge_documents
                    WHERE document_id = ?
                      AND status IN ('active', 'archived')
                    ORDER BY version DESC
                    """,
                    (document_id,),
                ).fetchall()
            ]

    def previous_version(self, document_id: str) -> Optional[dict[str, Any]]:
        """返回当前唯一可回滚的上一版。"""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE document_id = ? AND status = 'archived'
                ORDER BY version DESC LIMIT 1
                """,
                (document_id,),
            ).fetchone()
            return dict(row) if row else None

    def versions_to_prune(
        self,
        document_id: str,
        keep_version_ids: set[str],
    ) -> list[dict[str, Any]]:
        """列出切换前应清理的旧归档版本。"""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE document_id = ? AND status = 'archived'
                ORDER BY version DESC
                """,
                (document_id,),
            ).fetchall()
        return [
            dict(row) for row in rows
            if row["version_id"] not in keep_version_ids
        ]

    def document_versions(self, document_id: str) -> list[dict[str, Any]]:
        """返回逻辑文档的全部物理版本。"""
        with self._connection() as conn:
            return [
                dict(row) for row in conn.execute(
                    """
                    SELECT * FROM knowledge_documents
                    WHERE document_id = ? ORDER BY version DESC
                    """,
                    (document_id,),
                ).fetchall()
            ]

    def retention_cleanup_candidates(self) -> list[dict[str, Any]]:
        """列出两版本模型不再保留的归档版本。"""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE status IN ('active', 'archived')
                ORDER BY document_id, version DESC
                """
            ).fetchall()
        active_documents = {
            row["document_id"] for row in rows if row["status"] == "active"
        }
        kept_archived: set[str] = set()
        candidates = []
        for row in rows:
            if row["status"] != "archived":
                continue
            document_id = row["document_id"]
            if (
                document_id in active_documents
                and document_id not in kept_archived
            ):
                kept_archived.add(document_id)
                continue
            candidates.append(dict(row))
        return candidates

    def list_active(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [
                dict(row) for row in conn.execute(
                    """
                    SELECT * FROM knowledge_documents
                    WHERE status = 'active' ORDER BY document_id
                    """
                ).fetchall() if self._is_effective(row)
            ]

    def delete_version_record(self, version_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM knowledge_documents
                WHERE version_id = ? AND status IN ('archived', 'failed')
                """,
                (version_id,),
            )
            return cursor.rowcount > 0

    def delete_document_records(self, document_id: str) -> int:
        """删除逻辑文档的全部目录记录。"""
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM knowledge_documents WHERE document_id = ?",
                (document_id,),
            )
            return cursor.rowcount

    def register_legacy(self, metadata: dict[str, Any]) -> None:
        document_id = str(metadata.get("doc_id", ""))
        if not document_id:
            return
        with self._connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM knowledge_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if exists:
                return
            now = _now()
            conn.execute(
                """
                INSERT INTO knowledge_documents (
                    version_id, document_id, version, status, content_hash,
                    filename, doc_type, disease, source, authority_level,
                    created_at, activated_at
                ) VALUES (?, ?, 1, 'active', ?, ?, ?, ?, ?, 'legacy', ?, ?)
                """,
                (
                    document_id,
                    document_id,
                    metadata.get("content_hash") or f"legacy:{document_id}",
                    metadata.get("filename", ""),
                    metadata.get("type", "general"),
                    metadata.get("disease", ""),
                    metadata.get("source", ""),
                    now,
                    now,
                ),
            )

    @staticmethod
    def _is_effective(row: sqlite3.Row) -> bool:
        now = datetime.now(timezone.utc)
        effective_at = row["effective_at"]
        if effective_at:
            try:
                effective = datetime.fromisoformat(
                    str(effective_at).replace("Z", "+00:00")
                )
            except ValueError:
                return False
            if effective.tzinfo is None:
                effective = effective.replace(tzinfo=timezone.utc)
            if effective > now:
                return False
        expires_at = row["expires_at"]
        if not expires_at:
            return True
        try:
            expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > now

    def create_job(self, job_type: str, target_id: str, actor_id: str) -> str:
        job_id = "lifecycle_" + uuid.uuid4().hex
        now = _now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO lifecycle_jobs VALUES (?, ?, ?, 'pending', '{}', NULL, ?, ?, ?)
                """,
                (job_id, job_type, target_id, actor_id, now, now),
            )
        return job_id

    def finish_job(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any],
        error: Optional[str] = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE lifecycle_jobs SET status = ?, result = ?, error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, json.dumps(result, ensure_ascii=False), error, _now(), job_id),
            )

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM lifecycle_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["result"] = json.loads(item["result"])
            return item

    def audit(
        self,
        action: str,
        actor_id: str,
        target_id: str,
        result: dict[str, Any],
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO lifecycle_audit VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "audit_" + uuid.uuid4().hex,
                    action,
                    actor_id,
                    target_id,
                    json.dumps(result, ensure_ascii=False),
                    _now(),
                ),
            )
