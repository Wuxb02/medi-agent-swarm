"""索引 outbox 完整性扫描及管理员显式重试。"""

from .database import transaction
from .jobs import enqueue
from .metrics import increment

INDEX_KINDS = {"knowledge_index", "knowledge_delete", "session_index", "session_delete"}


async def reconcile() -> int:
    """修复缺失 outbox；已耗尽的任务保留失败状态，避免无限重试。"""
    repaired = 0
    async with transaction() as conn:
        await conn.execute(
            "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
        )
        rows = (
            await conn.execute(
                "SELECT d.* FROM knowledge_documents d WHERE d.status='indexing' "
                "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.dedup_key="
                "CONCAT('knowledge:',d.version_id)) FOR UPDATE"
            )
        ).fetchall()
        for row in rows:
            await enqueue(
                "knowledge_index",
                f"knowledge:{row['version_id']}",
                {
                    "version_id": row["version_id"],
                    "metadata": {
                        "filename": row["filename"],
                        "type": row["doc_type"],
                        "disease": row["disease"],
                        "source": row["source"],
                        "authority_level": row["authority_level"],
                        "effective_at": row["effective_at"],
                        "expires_at": row["expires_at"],
                    },
                },
            )
            repaired += 1
        await conn.execute(
            "UPDATE knowledge_documents d JOIN jobs j ON j.dedup_key="
            "CONCAT('knowledge:',d.version_id) SET d.status='failed',d.error=j.error "
            "WHERE d.status='indexing' AND j.status='failed'"
        )
        if repaired:
            await increment("index_outbox_repaired", repaired)
    return repaired


async def retry(job_id: str) -> dict:
    async with transaction() as conn:
        await conn.execute(
            "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
        )
        row = (
            await conn.execute(
                "SELECT * FROM jobs WHERE job_id=%s FOR UPDATE", (job_id,)
            )
        ).fetchone()
        if row is None or row["kind"] not in INDEX_KINDS:
            raise LookupError("索引任务不存在")
        if row["status"] != "failed":
            raise ValueError("只能重试失败的索引任务")
        if row["kind"] == "knowledge_index":
            from .jobs import decode

            decoded = decode(row)
            assert decoded is not None
            version_id = decoded["payload"]["version_id"]
            result = await conn.execute(
                "UPDATE knowledge_documents SET status='indexing',error=NULL "
                "WHERE version_id=%s AND status='failed'",
                (version_id,),
            )
            if result.rowcount != 1:
                raise ValueError("知识版本已删除或状态已变化")
        await conn.execute(
            "UPDATE jobs SET status='pending',attempts=0,token=token+1,owner=NULL,"
            "lease_until=NULL,error=NULL,scheduled_at=UTC_TIMESTAMP(6) WHERE job_id=%s",
            (job_id,),
        )
        await increment("index_manual_retry")
        return {"job_id": job_id, "status": "pending"}
