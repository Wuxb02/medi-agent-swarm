"""默认文档登记、重复启动及输入错误测试。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.knowledge import default_documents


@pytest.fixture
def storage(monkeypatch):
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=MagicMock())
    conn.execute.return_value.fetchone.return_value = None

    @asynccontextmanager
    async def transaction():
        yield conn

    catalog = MagicMock()
    catalog.begin_version = AsyncMock(return_value={"version_id": "version"})
    enqueue = AsyncMock()
    monkeypatch.setattr(default_documents, "transaction", transaction)
    monkeypatch.setattr(default_documents, "KnowledgeCatalog", lambda: catalog)
    monkeypatch.setattr(default_documents, "enqueue", enqueue)
    return conn, catalog, enqueue


async def test_all_bundled_documents_are_registered(storage):
    _, catalog, enqueue = storage
    files = list(default_documents.DOCUMENTS_DIR.glob("*.txt"))
    assert len(files) == 94
    assert await default_documents.seed_default_documents() == len(files)
    assert enqueue.await_count == len(files)
    metadata = [call.args[2] for call in catalog.begin_version.await_args_list]
    assert {item["type"] for item in metadata} == {
        "lifestyle", "symptoms", "disease_classification", "clinical_guideline"
    }
    assert all(item["authority_level"] == "user" for item in metadata)
    assert all(len(item["content_hash"]) == 64 for item in metadata)


async def test_existing_documents_are_not_overwritten(storage):
    conn, catalog, enqueue = storage
    conn.execute.return_value.fetchone.return_value = {"version_id": "existing"}
    assert await default_documents.seed_default_documents() == 0
    catalog.begin_version.assert_not_awaited()
    enqueue.assert_not_awaited()


async def test_missing_documents_fail_explicitly(tmp_path, storage):
    with pytest.raises(RuntimeError, match="目录为空"):
        await default_documents.seed_default_documents(tmp_path)


async def test_empty_document_fails(tmp_path, storage):
    (tmp_path / "01_lifestyle_测试.txt").write_text("  ", encoding="utf-8")
    with pytest.raises(ValueError, match="文档为空"):
        await default_documents.seed_default_documents(tmp_path)
    storage[2].assert_not_awaited()


async def test_enqueue_failure_is_propagated(storage):
    storage[2].side_effect = RuntimeError("索引任务登记失败")
    with pytest.raises(RuntimeError, match="索引任务登记失败"):
        await default_documents.seed_default_documents()
