"""验证历史分块统计回填与真实分块算法一致。"""

import importlib
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase

migration = importlib.import_module(
    "migrations.versions.0004_knowledge_chunk_count"
)


@pytest.mark.parametrize("has_column", [False, True])
def test_backfill_matches_chunking_and_preserves_existing_counts(
    monkeypatch, has_column
):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.connection.create_function("CHAR_LENGTH", 1, len)
        suffix = ", chunk_count INTEGER NOT NULL DEFAULT 0" if has_column else ""
        conn.exec_driver_sql(
            "CREATE TABLE knowledge_documents "
            f"(version_id TEXT, content TEXT, status TEXT{suffix})"
        )
        sizes = [0, 1, 924, 1024, 1025, 1848, 1849, 3000]
        for index, size in enumerate(sizes):
            conn.exec_driver_sql(
                "INSERT INTO knowledge_documents(version_id,content,status) "
                "VALUES (?,?,?)", (str(index), "医" * size, "active")
            )
        if has_column:
            conn.exec_driver_sql(
                "INSERT INTO knowledge_documents VALUES ('custom','内容','active',7)"
            )
        operations = MagicMock()
        operations.get_bind.return_value = conn
        operations.add_column.side_effect = lambda *args: conn.exec_driver_sql(
            "ALTER TABLE knowledge_documents ADD COLUMN "
            "chunk_count INTEGER NOT NULL DEFAULT 0"
        )
        operations.execute.side_effect = conn.exec_driver_sql
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        rows = conn.exec_driver_sql(
            "SELECT content,chunk_count FROM knowledge_documents "
            "WHERE version_id!='custom' ORDER BY version_id"
        ).all()
        assert all(
            count == len(MedicalKnowledgeBase._chunk_text(content))
            for content, count in rows
        )
        if has_column:
            assert conn.exec_driver_sql(
                "SELECT chunk_count FROM knowledge_documents WHERE version_id='custom'"
            ).scalar_one() == 7
        assert operations.add_column.call_count == int(not has_column)
    engine.dispose()


async def test_index_count_is_saved_in_activation_transaction(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from mediZJ.infrastructure import handlers

    version = {
        "version_id": "v1", "document_id": "doc", "version": 1,
        "status": "indexing", "content": "正文",
    }
    catalog = MagicMock()
    catalog.get_version = AsyncMock(return_value=version)
    catalog.activate = AsyncMock()
    catalog.retention_cleanup_candidates = AsyncMock(return_value=[])
    kb = MagicMock()
    kb.add_documents.return_value = 5
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=MagicMock())
    conn.execute.return_value.fetchone.return_value = None

    @asynccontextmanager
    async def transaction():
        yield conn

    monkeypatch.setattr(handlers, "transaction", transaction)
    monkeypatch.setattr(handlers, "assert_lease", AsyncMock())
    monkeypatch.setattr(
        "mediZJ.knowledge.catalog.KnowledgeCatalog", lambda: catalog
    )
    monkeypatch.setattr(
        "mediZJ.knowledge.milvus_kb.MedicalKnowledgeBase", lambda: kb
    )
    lineage = MagicMock(invalidate_document=AsyncMock())
    monkeypatch.setattr(
        "mediZJ.memory.lineage.MemoryLineageStore", lambda: lineage
    )
    await handlers.knowledge_index(
        {"payload": {"version_id": "v1", "metadata": {}}}
    )
    conn.execute.assert_any_await(
        "UPDATE knowledge_documents SET chunk_count=%s WHERE version_id=%s",
        (5, "v1"),
    )
    catalog.activate.assert_awaited_once_with("v1")
