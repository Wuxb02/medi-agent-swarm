"""非自进化数据的统一生命周期管理。"""

from mediZJ.infrastructure.database import transaction
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.memory.lineage import MemoryLineageStore
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.session_summary import DEFAULT_SESSION_SUMMARY_DIR


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
        job_id = await self.catalog.create_job("delete_user", user_id, actor_id)
        result: dict[str, Any] = {}
        errors: list[str] = []
        try:
            sessions = await self.session_db.list_sessions(
                limit=100000, user_id=user_id
            )
            session_ids = [item["session_id"] for item in sessions]
            from mediZJ.api.services.session_service import delete_session

            async with transaction():
                for session_id in session_ids:
                    await delete_session(session_id, user_id)
                result["sessions"] = len(session_ids)
                result.update(await self._delete_local_user_rows(user_id))
            result["memory_lineage"] = await MemoryLineageStore().delete_user(user_id)
            status = "failed" if errors else "completed"
            (
                await self.catalog.finish_job(
                    job_id, status, result, "; ".join(errors) or None
                )
            )
            (await self.catalog.audit("delete_user", actor_id, user_id, result))
        except Exception as exc:
            (await self.catalog.finish_job(job_id, "failed", result, str(exc)))
        return (await self.catalog.get_job(job_id)) or {"job_id": job_id}

    async def prune_expired(self, actor_id: str) -> dict[str, Any]:
        job_id = await self.catalog.create_job("prune_expired", "", actor_id)
        try:
            result = await self._prune_memory_rows()
            (await self.catalog.finish_job(job_id, "completed", result))
            (await self.catalog.audit("prune_expired", actor_id, "", result))
        except Exception as exc:
            (await self.catalog.finish_job(job_id, "failed", {}, str(exc)))
        return (await self.catalog.get_job(job_id)) or {"job_id": job_id}

    async def retry(self, job_id: str, actor_id: str) -> dict[str, Any]:
        job = await self.catalog.get_job(job_id)
        if not job or job["status"] != "failed":
            raise LookupError("失败的清理作业不存在")
        if job["job_type"] == "delete_user":
            return await self.delete_user(job["target_id"], actor_id)
        return await self.prune_expired(actor_id)

    async def _delete_local_user_rows(self, user_id: str) -> dict[str, int]:
        result: dict[str, int] = {}
        async with transaction() as conn:
            for table, field in (
                ("memory_usage", "user_id"),
                ("memory_audit", "user_id"),
                ("episodic_summaries", "user_id"),
                ("memory_profile_revisions", "user_id"),
                ("user_memory_items", "user_id"),
                ("profiles", "user_id"),
                ("auth_sessions", "user_id"),
                ("traces", "user_id"),
            ):
                cursor = await conn.execute(
                    f"DELETE FROM {table} WHERE {field} = %s", (user_id,)
                )
                result[table] = cursor.rowcount
        return result

    async def _prune_memory_rows(self) -> dict[str, int]:
        """清理过期记忆和超期审计记录。"""
        now = datetime.now(timezone.utc).isoformat()
        audit_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        result: dict[str, int] = {}
        async with transaction() as conn:
            cursor = await conn.execute(
                """
                UPDATE user_memory_items
                SET status = 'stale', updated_at = %s
                WHERE status = 'active' AND expires_at IS NOT NULL
                  AND expires_at <= %s
                """,
                (now, now),
            )
            result["user_memory_items"] = cursor.rowcount
            cursor = await conn.execute(
                """
                UPDATE episodic_summaries
                SET status = 'expired', updated_at = %s
                WHERE status = 'active' AND expires_at IS NOT NULL
                  AND expires_at <= %s
                """,
                (now, now),
            )
            result["episodic_summaries"] = cursor.rowcount
            cursor = await conn.execute(
                "DELETE FROM memory_usage WHERE created_at <= %s",
                (audit_cutoff,),
            )
            result["memory_usage"] = cursor.rowcount
        result["knowledge_documents"] = await self.catalog.expire_documents(now)
        return result

    @staticmethod
    def _delete_summary_files(session_id: str) -> None:
        base = Path(DEFAULT_SESSION_SUMMARY_DIR)
        for path in base.glob(f"*{session_id}*"):
            if path.is_file():
                path.unlink(missing_ok=True)
