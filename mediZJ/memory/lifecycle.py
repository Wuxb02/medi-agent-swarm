"""非自进化数据的统一生命周期管理。"""

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
from mediZJ.memory.lineage import MemoryLineageStore
from mediZJ.memory.long_term import LongTermMemory
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.session_summary import DEFAULT_SESSION_SUMMARY_DIR
from mediZJ.memory.session_vector_store import SessionVectorStore


_lifecycle_task: asyncio.Task | None = None
_lifecycle_stop: asyncio.Event | None = None


async def start_lifecycle_worker() -> None:
    """启动每日 TTL 清理工作器。"""
    global _lifecycle_task, _lifecycle_stop
    if _lifecycle_task is not None:
        return
    if os.getenv("DATA_LIFECYCLE_ENABLED", "true").lower() not in {
        "1", "true", "yes",
    }:
        return
    _lifecycle_stop = asyncio.Event()
    _lifecycle_task = asyncio.create_task(_lifecycle_loop())


async def stop_lifecycle_worker() -> None:
    global _lifecycle_task
    if _lifecycle_task is None:
        return
    assert _lifecycle_stop is not None
    _lifecycle_stop.set()
    await _lifecycle_task
    _lifecycle_task = None


async def _lifecycle_loop() -> None:
    assert _lifecycle_stop is not None
    interval = int(os.getenv("DATA_LIFECYCLE_INTERVAL_SECONDS", "86400"))
    while not _lifecycle_stop.is_set():
        try:
            await asyncio.wait_for(_lifecycle_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            await DataLifecycleService().prune_expired("system")


class DataLifecycleService:
    """持久化作业状态，不读写自进化表。"""

    def __init__(
        self,
        catalog: KnowledgeCatalog | None = None,
        session_db: SessionDB | None = None,
    ) -> None:
        self.catalog = catalog or KnowledgeCatalog()
        self.session_db = session_db or SessionDB()

    async def delete_user(self, user_id: str, actor_id: str) -> dict[str, Any]:
        job_id = self.catalog.create_job("delete_user", user_id, actor_id)
        result: dict[str, Any] = {}
        errors: list[str] = []
        try:
            sessions = self.session_db.list_sessions(
                limit=100000, user_id=user_id
            )
            session_ids = [item["session_id"] for item in sessions]
            for session_id in session_ids:
                try:
                    SessionVectorStore().delete_session(session_id)
                    result["session_vectors"] = result.get("session_vectors", 0) + 1
                except Exception as exc:
                    errors.append(f"session_vector:{session_id}:{exc}")
                self._delete_summary_files(session_id)
            result.update(self._delete_local_user_rows(user_id))
            result["memory_lineage"] = MemoryLineageStore(
                self.session_db.db_path
            ).delete_user(user_id)
            mem0_error = await self._delete_mem0(user_id)
            if mem0_error:
                errors.append(mem0_error)
            status = "failed" if errors else "completed"
            self.catalog.finish_job(
                job_id, status, result, "; ".join(errors) or None
            )
            self.catalog.audit("delete_user", actor_id, user_id, result)
        except Exception as exc:
            self.catalog.finish_job(job_id, "failed", result, str(exc))
        return self.catalog.get_job(job_id) or {"job_id": job_id}

    async def prune_expired(self, actor_id: str) -> dict[str, Any]:
        job_id = self.catalog.create_job("prune_expired", "", actor_id)
        retention_days = int(os.getenv("KNOWLEDGE_ARCHIVE_RETENTION_DAYS", "365"))
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=retention_days)
        ).isoformat()
        removed = 0
        try:
            kb = MedicalKnowledgeBase()
            for version in self.catalog.archived_before(cutoff):
                kb.delete_document(version["version_id"])
                if self.catalog.delete_version_record(version["version_id"]):
                    removed += 1
            result = {"knowledge_versions": removed}
            self.catalog.finish_job(job_id, "completed", result)
            self.catalog.audit("prune_expired", actor_id, "", result)
        except Exception as exc:
            self.catalog.finish_job(job_id, "failed", {"knowledge_versions": removed}, str(exc))
        return self.catalog.get_job(job_id) or {"job_id": job_id}

    async def retry(self, job_id: str, actor_id: str) -> dict[str, Any]:
        job = self.catalog.get_job(job_id)
        if not job or job["status"] != "failed":
            raise LookupError("失败的清理作业不存在")
        if job["job_type"] == "delete_user":
            return await self.delete_user(job["target_id"], actor_id)
        return await self.prune_expired(actor_id)

    def _delete_local_user_rows(self, user_id: str) -> dict[str, int]:
        result: dict[str, int] = {}
        with sqlite3.connect(self.session_db.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            for table, field in (
                ("sessions", "user_id"),
                ("profiles", "user_id"),
                ("auth_sessions", "user_id"),
                ("traces", "user_id"),
            ):
                try:
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE {field} = ?", (user_id,)
                    )
                    result[table] = cursor.rowcount
                except sqlite3.OperationalError:
                    result[table] = 0
        return result

    @staticmethod
    def _delete_summary_files(session_id: str) -> None:
        base = Path(DEFAULT_SESSION_SUMMARY_DIR)
        for path in base.glob(f"*{session_id}*"):
            if path.is_file():
                path.unlink(missing_ok=True)

    @staticmethod
    async def _delete_mem0(user_id: str) -> str | None:
        memory = LongTermMemory(user_id=user_id)
        if not memory.enabled:
            return None
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    memory.mem0.delete_all,
                    user_id=f"mediZJ_user_{user_id}",
                ),
                timeout=10,
            )
            return None
        except Exception as exc:
            return f"mem0:{exc}"
