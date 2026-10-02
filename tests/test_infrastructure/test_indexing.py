"""真实 Milvus 索引重试、乱序、幂等与 MySQL 检索边界。"""

import asyncio
import uuid
from unittest.mock import patch

import numpy as np
import pytest

from mediZJ.api.services import knowledge_service
from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.handlers import knowledge_delete, knowledge_index
from mediZJ.infrastructure.indexing import reconcile, retry
from mediZJ.infrastructure.jobs import JobWorker, claim
from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


class Embedding:
    """只替换 embedding 边界，Milvus upsert 与混合检索均使用真实服务。"""

    def get_sentence_embedding_dimension(self):
        return 512

    def encode(self, texts, **kwargs):
        return np.array([[0.01] * 512 for _ in texts])


@pytest.fixture
async def knowledge(mysql_infrastructure, monkeypatch):
    MedicalKnowledgeBase._instance = None
    monkeypatch.setattr(
        "mediZJ.knowledge.milvus_kb.load_embedding_model", lambda _: Embedding()
    )
    kb = await asyncio.to_thread(
        MedicalKnowledgeBase,
        collection_name="test_" + uuid.uuid4().hex,
        initialize=True,
    )
    yield kb
    await asyncio.to_thread(kb.milvus_client.drop_collection, kb.collection_name)
    await asyncio.to_thread(kb.milvus_client.close)
    MedicalKnowledgeBase._instance = None


async def ingest(content, key):
    return (
        await knowledge_service._ingest_version(
            "doc",
            content,
            {
                "content_hash": key,
                "filename": "guide.txt",
                "type": "clinical_guideline",
                "authority_level": "authoritative",
                "source": "临床指南数据库",
            },
        )
    )[1]


async def test_milvus_failure_recovers_without_duplicate_vectors(knowledge):
    pending = await ingest("高血压应定期随访。", "hash-1")
    worker = JobWorker({"knowledge_index": knowledge_index})
    job = await claim("knowledge_index", worker.owner)
    with patch.object(knowledge, "add_documents", side_effect=ConnectionError):
        await worker._execute(job)
    assert (await KnowledgeCatalog().get_version(pending["version_id"]))[
        "status"
    ] == "indexing"
    async with transaction() as conn:
        await conn.execute(
            "UPDATE jobs SET scheduled_at=UTC_TIMESTAMP(6) WHERE job_id=%s",
            (job["job_id"],),
        )
    retried = await claim("knowledge_index", worker.owner)
    assert retried["attempts"] == 2
    await knowledge_index(retried)
    await knowledge_index(retried)
    await worker._execute(retried)
    catalog = KnowledgeCatalog()
    assert (await catalog.active_version("doc"))["version_id"] == pending["version_id"]
    rows = await asyncio.to_thread(
        knowledge.milvus_client.query,
        knowledge.collection_name,
        filter=f'doc_id == "{pending["version_id"]}"',
        output_fields=["id"],
        consistency_level="Strong",
    )
    assert len(rows) == 1
    assert await knowledge_service.search_knowledge("高血压")
    async with transaction() as conn:
        await conn.execute(
            "UPDATE knowledge_documents SET status='archived' WHERE version_id=%s",
            (pending["version_id"],),
        )
    assert await knowledge_service.search_knowledge("高血压") == []


async def test_late_old_index_cannot_replace_new_version(knowledge):
    first = await ingest("旧知识原文。", "hash-1")
    second = await ingest("新知识原文。", "hash-2")
    old = await claim("knowledge_index", "old")
    new = await claim("knowledge_index", "new")
    await knowledge_index(new)
    await knowledge_index(old)
    assert (await KnowledgeCatalog().active_version("doc"))["version_id"] == second[
        "version_id"
    ]
    assert await KnowledgeCatalog().get_version(first["version_id"]) is None
    deletion = await claim("knowledge_delete", "delete")
    await knowledge_delete(deletion)
    await knowledge_delete(deletion)


async def test_missing_outbox_and_exhausted_task_are_recoverable(knowledge):
    version = await ingest("原文。", "hash-1")
    async with transaction() as conn:
        await conn.execute("DELETE FROM jobs WHERE kind='knowledge_index'")
    assert await reconcile() == 1
    assert await reconcile() == 0
    worker = JobWorker({"knowledge_index": knowledge_index})
    with patch.object(knowledge, "add_documents", side_effect=ConnectionError):
        for attempt in range(3):
            job = await claim("knowledge_index", worker.owner)
            await worker._execute(job)
            async with transaction() as conn:
                await conn.execute(
                    "UPDATE jobs SET scheduled_at=UTC_TIMESTAMP(6) WHERE job_id=%s",
                    (job["job_id"],),
                )
    assert (await KnowledgeCatalog().get_version(version["version_id"]))[
        "status"
    ] == "failed"
    assert await claim("knowledge_index", "extra") is None
    assert (await retry(job["job_id"]))["status"] == "pending"
    retried = await claim("knowledge_index", worker.owner)
    await worker._execute(retried)
    assert (await KnowledgeCatalog().active_version("doc"))["version_id"] == version[
        "version_id"
    ]


async def test_session_vectors_upsert_search_owner_and_delete(
    mysql_infrastructure, monkeypatch
):
    from mediZJ.memory.session_vector_store import SessionVectorStore
    from mediZJ.infrastructure.settings import get_settings

    SessionVectorStore._instance = None
    get_settings().session_collection = "session_test_" + uuid.uuid4().hex
    monkeypatch.setattr(
        "mediZJ.memory.session_vector_store.load_embedding_model", lambda _: Embedding()
    )
    store = await asyncio.to_thread(SessionVectorStore, initialize=True)
    try:
        await asyncio.to_thread(store.index_session, "session", "高血压随访", "alice")
        await asyncio.to_thread(store.index_session, "session", "高血压更新", "alice")
        hits = await asyncio.to_thread(store.search_similar, "高血压", 3, "alice")
        assert len(hits) == 1 and hits[0]["session_id"] == "session"
        assert await asyncio.to_thread(store.search_similar, "高血压", 3, "bob") == []
        assert await asyncio.to_thread(store.count_sessions) == 1
        await asyncio.to_thread(store.delete_session, "session")
        await asyncio.to_thread(store.delete_session, "session")
        rows = await asyncio.to_thread(
            store.milvus_client.query,
            store.collection_name,
            filter='id == "session"',
            consistency_level="Strong",
        )
        assert rows == []
    finally:
        await asyncio.to_thread(
            store.milvus_client.drop_collection, store.collection_name
        )
        await asyncio.to_thread(store.milvus_client.close)
        SessionVectorStore._instance = None
