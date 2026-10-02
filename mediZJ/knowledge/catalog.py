"""知识文档版本目录。"""

from mediZJ.infrastructure.database import transaction

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeCatalog:
    """管理逻辑文档与物理版本，确保激活切换原子化。"""

    _instance: Optional["KnowledgeCatalog"] = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """存储实例不在构造阶段访问数据库。"""
        self._initialized = True

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def _connection(self):
        return transaction()

    async def begin_version(
        self,
        document_id: str,
        content_hash: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """在串行事务中分配下一版本。"""
        async with self._connection() as conn:
            await conn.execute(
                "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
            )
            duplicate = (
                await conn.execute(
                    """
                SELECT * FROM knowledge_documents
                WHERE content_hash = %s AND status != 'failed'
                LIMIT 1
                """,
                    (content_hash,),
                )
            ).fetchone()
            if duplicate:
                raise ValueError(f"内容相同的文档已存在: {duplicate['filename']}")
            previous = (
                await conn.execute(
                    """
                SELECT * FROM knowledge_documents
                WHERE document_id = %s AND status IN ('active', 'expired')
                ORDER BY version DESC LIMIT 1
                """,
                    (document_id,),
                )
            ).fetchone()
            maximum = (
                await conn.execute(
                    "SELECT MAX(version) AS value FROM knowledge_documents WHERE document_id = %s",
                    (document_id,),
                )
            ).fetchone()["value"]
            version = int(maximum or 0) + 1
            version_id = f"kv_{uuid.uuid4().hex}"
            created_at = _now()
            (
                await conn.execute(
                    """
                INSERT INTO knowledge_documents (
                    version_id, document_id, version, status,
                    supersedes_version_id, content_hash, filename, doc_type,
                    disease, source, authority_level, effective_at, expires_at,
                    created_at
                ) VALUES (%s, %s, %s, 'indexing', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            )
            return dict(
                (
                    await conn.execute(
                        "SELECT * FROM knowledge_documents WHERE version_id = %s",
                        (version_id,),
                    )
                ).fetchone()
            )

    async def activate(self, version_id: str) -> dict[str, Any]:
        async with self._connection() as conn:
            await conn.execute(
                "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
            )
            row = (
                await conn.execute(
                    "SELECT * FROM knowledge_documents WHERE version_id = %s",
                    (version_id,),
                )
            ).fetchone()
            if not row:
                raise LookupError("知识文档版本不存在")
            (
                await conn.execute(
                    """
                UPDATE knowledge_documents SET status = 'archived'
                WHERE document_id = %s AND status IN ('active', 'expired')
                  AND version_id != %s
                """,
                    (row["document_id"], version_id),
                )
            )
            (
                await conn.execute(
                    """
                UPDATE knowledge_documents
                SET status = 'active', activated_at = %s, error = NULL
                WHERE version_id = %s
                """,
                    (_now(), version_id),
                )
            )
            return dict(
                (
                    await conn.execute(
                        "SELECT * FROM knowledge_documents WHERE version_id = %s",
                        (version_id,),
                    )
                ).fetchone()
            )

    async def mark_failed(self, version_id: str, error: str) -> None:
        async with self._connection() as conn:
            (
                await conn.execute(
                    """
                UPDATE knowledge_documents
                SET status = 'failed', error = %s WHERE version_id = %s
                """,
                    (error[:1000], version_id),
                )
            )

    async def active_version(self, document_id: str) -> Optional[dict[str, Any]]:
        async with self._connection() as conn:
            row = (
                await conn.execute(
                    """
                SELECT * FROM knowledge_documents
                WHERE document_id = %s AND status = 'active'
                """,
                    (document_id,),
                )
            ).fetchone()
            return dict(row) if row and self._is_effective(row) else None

    async def active_by_version(self, version_id: str) -> Optional[dict[str, Any]]:
        async with self._connection() as conn:
            row = (
                await conn.execute(
                    """
                SELECT * FROM knowledge_documents
                WHERE version_id = %s AND status = 'active'
                """,
                    (version_id,),
                )
            ).fetchone()
            return dict(row) if row and self._is_effective(row) else None

    async def get_version(self, version_id: str) -> Optional[dict[str, Any]]:
        async with self._connection() as conn:
            await conn.execute(
                "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
            )
            row = (
                await conn.execute(
                    "SELECT * FROM knowledge_documents WHERE version_id = %s",
                    (version_id,),
                )
            ).fetchone()
            return dict(row) if row else None

    async def list_versions(self, document_id: str) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            return [
                dict(row)
                for row in (
                    await conn.execute(
                        """
                    SELECT * FROM knowledge_documents
                    WHERE document_id = %s
                      AND status IN ('active', 'archived', 'expired')
                    ORDER BY version DESC
                    """,
                        (document_id,),
                    )
                ).fetchall()
            ]

    async def previous_version(self, document_id: str) -> Optional[dict[str, Any]]:
        """返回当前唯一可回滚的上一版。"""
        async with self._connection() as conn:
            row = (
                await conn.execute(
                    """
                SELECT * FROM knowledge_documents
                WHERE document_id = %s AND status = 'archived'
                ORDER BY version DESC LIMIT 1
                """,
                    (document_id,),
                )
            ).fetchone()
            return dict(row) if row else None

    async def versions_to_prune(
        self,
        document_id: str,
        keep_version_ids: set[str],
    ) -> list[dict[str, Any]]:
        """列出切换前应清理的旧归档版本。"""
        async with self._connection() as conn:
            rows = (
                await conn.execute(
                    """
                SELECT * FROM knowledge_documents
                WHERE document_id = %s AND status = 'archived'
                ORDER BY version DESC
                """,
                    (document_id,),
                )
            ).fetchall()
        return [dict(row) for row in rows if row["version_id"] not in keep_version_ids]

    async def document_versions(self, document_id: str) -> list[dict[str, Any]]:
        """返回逻辑文档的全部物理版本。"""
        async with self._connection() as conn:
            return [
                dict(row)
                for row in (
                    await conn.execute(
                        """
                    SELECT * FROM knowledge_documents
                    WHERE document_id = %s ORDER BY version DESC
                    """,
                        (document_id,),
                    )
                ).fetchall()
            ]

    async def retention_cleanup_candidates(self) -> list[dict[str, Any]]:
        """列出两版本模型不再保留的归档版本。"""
        async with self._connection() as conn:
            rows = (
                await conn.execute(
                    """
                SELECT * FROM knowledge_documents
                WHERE status IN ('active', 'archived')
                ORDER BY document_id, version DESC
                """
                )
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
            if document_id in active_documents and document_id not in kept_archived:
                kept_archived.add(document_id)
                continue
            candidates.append(dict(row))
        return candidates

    async def expire_documents(self, now: str) -> int:
        """将已到期但仍为 active 的版本标记为 expired，返回更新数量。"""
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE knowledge_documents
                SET status = 'expired'
                WHERE status = 'active' AND expires_at IS NOT NULL
                  AND expires_at <= %s
                """,
                (now,),
            )
            return cursor.rowcount

    async def list_active(self) -> list[dict[str, Any]]:
        async with self._connection() as conn:
            return [
                dict(row)
                for row in (
                    await conn.execute(
                        """
                    SELECT * FROM knowledge_documents
                    WHERE status = 'active' ORDER BY document_id
                    """
                    )
                ).fetchall()
                if self._is_effective(row)
            ]

    async def list_active_and_expired(self) -> list[dict[str, Any]]:
        """返回现行版本（有效或已过期）供管理端展示。

        包含 status='expired' 的全部记录，以及 status='active' 的记录；
        对 active 记录复用 _is_effective 判断，将已到期但尚未被作业标记的
        版本在展示层映射为 expired，使列表实时反映真实效力。
        """
        async with self._connection() as conn:
            rows = (
                await conn.execute(
                    """
                SELECT version_id, document_id, version, status, filename,
                       doc_type, disease, source, authority_level, effective_at,
                       expires_at, chunk_count
                FROM knowledge_documents
                WHERE status IN ('active', 'expired')
                ORDER BY document_id, version DESC
                """
                )
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if item["status"] == "active" and not self._is_effective(row):
                item["status"] = "expired"
            result.append(item)
        return result

    async def delete_version_record(self, version_id: str) -> bool:
        async with self._connection() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM knowledge_documents
                WHERE version_id = %s AND status IN ('archived', 'failed')
                """,
                (version_id,),
            )
            return cursor.rowcount > 0

    async def delete_document_records(self, document_id: str) -> int:
        """删除逻辑文档的全部目录记录。"""
        async with self._connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM knowledge_documents WHERE document_id = %s",
                (document_id,),
            )
            return cursor.rowcount

    @staticmethod
    def _is_effective(row: dict[str, Any]) -> bool:
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

    async def create_job(self, job_type: str, target_id: str, actor_id: str) -> str:
        job_id = "lifecycle_" + uuid.uuid4().hex
        now = _now()
        async with self._connection() as conn:
            (
                await conn.execute(
                    """
                INSERT INTO lifecycle_jobs VALUES (%s, %s, %s, 'pending', '{}', NULL, %s, %s, %s)
                """,
                    (job_id, job_type, target_id, actor_id, now, now),
                )
            )
        return job_id

    async def finish_job(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any],
        error: Optional[str] = None,
    ) -> None:
        async with self._connection() as conn:
            (
                await conn.execute(
                    """
                UPDATE lifecycle_jobs SET status = %s, result = %s, error = %s, updated_at = %s
                WHERE job_id = %s
                """,
                    (
                        status,
                        json.dumps(result, ensure_ascii=False),
                        error,
                        _now(),
                        job_id,
                    ),
                )
            )

    async def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        async with self._connection() as conn:
            row = (
                await conn.execute(
                    "SELECT * FROM lifecycle_jobs WHERE job_id = %s", (job_id,)
                )
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["result"] = json.loads(item["result"])
            return item

    async def audit(
        self,
        action: str,
        actor_id: str,
        target_id: str,
        result: dict[str, Any],
    ) -> None:
        async with self._connection() as conn:
            (
                await conn.execute(
                    "INSERT INTO lifecycle_audit VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        "audit_" + uuid.uuid4().hex,
                        action,
                        actor_id,
                        target_id,
                        json.dumps(result, ensure_ascii=False),
                        _now(),
                    ),
                )
            )
