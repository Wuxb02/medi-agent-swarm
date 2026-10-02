"""统一后台任务处理器。"""

import asyncio

from .database import transaction
from .jobs import assert_lease, decode, enqueue


async def memory(job):
    from mediZJ.swarm.swarm_coordinator import SwarmCoordinator

    async with transaction() as conn:
        row = decode(
            (
                await conn.execute(
                    "SELECT * FROM chat_runs WHERE run_id=%s",
                    (job["payload"]["run_id"],),
                )
            ).fetchone()
        )
    if row is None:
        return
    coordinator = SwarmCoordinator(user_id=row["user_id"])
    await coordinator._save_memory_candidates(
        row["session_id"],
        row["request"]["question"],
        row["result"]["answer"],
        {"trace_id": row["run_id"]},
    )


async def session_index(job):
    from mediZJ.memory.session_db import SessionDB
    from mediZJ.memory.session_vector_store import SessionVectorStore

    payload = job["payload"]
    session = await SessionDB().get_session(payload["session_id"], payload["user_id"])
    if session:
        await asyncio.to_thread(
            SessionVectorStore().index_session,
            session["session_id"],
            f"会话共 {session['turn_count']} 轮。首问：{session['first_question']}",
            session["user_id"],
            session["mode"],
            session["created_at"],
            session["total_tokens"],
        )
    async with transaction():
        current = await SessionDB().get_session(
            payload["session_id"], payload["user_id"]
        )
        if current is None:
            await enqueue(
                "session_delete", f"session-delete-after-index:{job['job_id']}", payload
            )
        elif session is not None and current["turn_count"] != session["turn_count"]:
            await enqueue(
                "session_index",
                f"session-index-repair:{job['job_id']}:{current['turn_count']}",
                payload,
            )


async def cache_delete(job):
    from mediZJ.memory.short_term import ShortTermMemory

    await ShortTermMemory(job["payload"]["user_id"]).clear_session(
        job["payload"]["session_id"]
    )


async def session_delete(job):
    from mediZJ.memory.session_vector_store import SessionVectorStore

    await asyncio.to_thread(
        SessionVectorStore().delete_session, job["payload"]["session_id"]
    )


async def complete_cleanup(job):
    audit_id = job["payload"].get("audit_id")
    if not audit_id:
        return
    async with transaction() as conn:
        await conn.execute(
            "SELECT audit_id FROM session_deletion_audits WHERE audit_id=%s FOR UPDATE",
            (audit_id,),
        )
        pending = (
            await conn.execute(
                "SELECT job_id FROM jobs WHERE dedup_key IN (%s,%s) "
                "AND job_id!=%s AND status!='completed' LIMIT 1",
                (
                    f"cache-delete:{audit_id}",
                    f"vector-delete:{audit_id}",
                    job["job_id"],
                ),
            )
        ).fetchone()
        if pending is None:
            from mediZJ.evolution.storage import EvolutionStorage

            await EvolutionStorage().complete_session_cleanup(audit_id, [])


async def knowledge_index(job):
    from mediZJ.knowledge.catalog import KnowledgeCatalog
    from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
    from mediZJ.memory.lineage import MemoryLineageStore

    catalog = KnowledgeCatalog()
    version = await catalog.get_version(job["payload"]["version_id"])
    if version is None or version["status"] != "indexing":
        return
    metadata = {
        **job["payload"]["metadata"],
        "document_id": version["document_id"],
        "version_id": version["version_id"],
        "document_version": version["version"],
    }
    kb = MedicalKnowledgeBase()
    chunk_count = await asyncio.to_thread(
        kb.add_documents,
        [
            {
                "id": version["version_id"],
                "content": version["content"],
                "metadata": metadata,
            },
        ],
    )
    async with transaction() as conn:
        await assert_lease(conn, job)
        await conn.execute(
            "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
        )
        current = await catalog.get_version(version["version_id"])
        if current is None or current["status"] != "indexing":
            await enqueue(
                "knowledge_delete",
                f"knowledge-delete:{version['version_id']}",
                {"version_id": version["version_id"]},
            )
            return
        newer = (
            await conn.execute(
                "SELECT version_id FROM knowledge_documents WHERE document_id=%s "
                "AND status IN ('active','archived','expired') AND version>%s LIMIT 1",
                (version["document_id"], version["version"]),
            )
        ).fetchone()
        if newer:
            await conn.execute(
                "UPDATE knowledge_documents SET status='archived' WHERE version_id=%s",
                (version["version_id"],),
            )
            await enqueue(
                "knowledge_delete",
                f"knowledge-delete:{version['version_id']}",
                {"version_id": version["version_id"]},
            )
            await catalog.delete_version_record(version["version_id"])
            return
        await conn.execute(
            "UPDATE knowledge_documents SET chunk_count=%s WHERE version_id=%s",
            (chunk_count, version["version_id"]),
        )
        await catalog.activate(version["version_id"])
        await MemoryLineageStore().invalidate_document(
            version["document_id"], "document_superseded"
        )
        for old in await catalog.retention_cleanup_candidates():
            if old["document_id"] == version["document_id"]:
                await enqueue(
                    "knowledge_delete",
                    f"knowledge-delete:{old['version_id']}",
                    {"version_id": old["version_id"]},
                )
                await catalog.delete_version_record(old["version_id"])


async def knowledge_delete(job):
    from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase

    await asyncio.to_thread(
        MedicalKnowledgeBase().delete_document, job["payload"]["version_id"]
    )


async def evaluation(job):
    import json
    from mediZJ.evolution import EvolutionService

    service = EvolutionService()
    async with transaction() as conn:
        row = decode(
            (
                await conn.execute(
                    "SELECT * FROM evaluation_jobs WHERE job_id=%s FOR UPDATE",
                    (job["payload"]["evaluation_job_id"],),
                )
            ).fetchone()
        )
        if row is None or row["status"] in {"superseded", "completed"}:
            return
        await conn.execute(
            "UPDATE evaluation_jobs SET status='running',attempts=%s WHERE job_id=%s",
            (job["attempts"], row["job_id"]),
        )
    context = await service.storage.get_message_context(
        row["assistant_message_id"], row["user_id"]
    )
    if context is None:
        return
    if row.get("feedback_snapshot"):
        context["feedback"] = json.loads(row["feedback_snapshot"])
    result = await asyncio.wait_for(
        service.judge.evaluate(context), service.settings.judge_timeout
    )
    async with transaction() as conn:
        await assert_lease(conn, job)
        await service.storage.save_evaluation(row, result, service.judge.model_name)


async def lifecycle(job):
    from mediZJ.memory.lifecycle import DataLifecycleService

    result = await DataLifecycleService().prune_expired("system")
    if result["status"] == "failed":
        raise RuntimeError("生命周期清理失败")


def get_handlers():
    from mediZJ.api.services.run_service import execute_run

    return {
        "chat": execute_run,
        "memory": memory,
        "evaluation": evaluation,
        "session_index": session_index,
        "session_delete": session_delete,
        "cache_delete": cache_delete,
        "knowledge_index": knowledge_index,
        "knowledge_delete": knowledge_delete,
        "lifecycle": lifecycle,
    }
