"""验证知识服务仅公开当前有效版本，版本变更保留来源元数据。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.api.services import knowledge_service as service


@pytest.fixture
def knowledge(monkeypatch):
    version = {
        "document_id": "doc",
        "version_id": "v1",
        "version": 1,
        "chunk_count": 1,
        "filename": "指南.txt",
        "doc_type": "clinical_guideline",
        "disease": "",
        "source": "可信指南",
        "status": "active",
        "effective_at": None,
        "expires_at": None,
        "authority_level": "guideline",
    }
    catalog = MagicMock(
        active_version=AsyncMock(return_value=version),
        list_active=AsyncMock(return_value=[version]),
        list_active_and_expired=AsyncMock(return_value=[version]),
    )
    kb = MagicMock(
        search=AsyncMock(),
        get_document_chunks=MagicMock(
            return_value=[
                {
                    "milvus_id": "v1:0",
                    "chunk_id": 0,
                    "content": "来源",
                    "total_chunks": 1,
                }
            ]
        ),
    )
    monkeypatch.setattr(service, "MedicalKnowledgeBase", lambda: kb)
    monkeypatch.setattr(service, "_get_catalog", AsyncMock(return_value=catalog))
    monkeypatch.setattr(service, "KnowledgeCatalog", lambda: catalog)
    return kb, catalog, version


async def test_search_revalidates_versions_and_limits_results(knowledge):
    kb, catalog, version = knowledge
    kb.search.return_value = [
        {"id": "old", "content": "旧来源", "metadata": {"version_id": "old"}},
        {"id": "valid", "content": "当前来源", "metadata": {"version_id": "v1"}},
        {"id": "extra", "content": "多余", "metadata": {"version_id": "v1"}},
    ]
    catalog.active_by_version = AsyncMock(side_effect=[None, version])
    items = await service.search_knowledge("问题", top_k=1)
    assert len(items) == 1 and items[0].id == "valid"
    assert items[0].metadata["authority_level"] == "guideline"
    assert catalog.active_by_version.await_count == 2


async def test_document_views_keep_version_metadata(knowledge):
    kb, catalog, version = knowledge
    assert await service.get_knowledge_base_size() == 1
    documents = await service.list_all_documents()
    assert documents.documents[0].version_id == "v1"
    assert documents.documents[0].chunk_count == 1
    kb.get_document_chunks.assert_not_called()
    detail = await service.get_document_chunks("doc")
    assert detail.total == 1
    assert detail.chunks[0].milvus_id == "v1:0"
    assert detail.chunks[0].content == "来源"
    catalog.active_version.return_value = None
    assert (await service.get_document_chunks("missing")).total == 0
    assert service.get_knowledge_types()


async def test_ingestion_preserves_source_and_content_hash(knowledge, monkeypatch):
    _, catalog, version = knowledge
    ingest = AsyncMock(return_value=(0, version))
    monkeypatch.setattr(service, "_ingest_version", ingest)
    uploaded = await service.upload_document("指南.txt", "医学内容")
    assert uploaded.indexing_status == "indexing" and uploaded.chunks_added == 0
    assert len(ingest.await_args.args[2]["content_hash"]) == 64
    updated = await service.update_document("doc", "更新内容")
    assert updated.filename == "指南.txt"
    assert ingest.await_args.args[2]["source"] == "可信指南"
    assert ingest.await_args.args[2]["authority_level"] == "guideline"
    catalog.active_version.return_value = None
    with pytest.raises(ValueError, match="Document not found"):
        await service.update_document("missing", "内容")


@pytest.mark.parametrize(
    "effective,expires",
    [
        ("invalid", "2099-01-01"),
        (None, "invalid"),
        ("2099-02-01", "2099-01-01"),
        (None, "2000-01-01"),
    ],
)
def test_time_ranges_reject_invalid_or_expired_dates(effective, expires):
    with pytest.raises(ValueError):
        service._validate_time_range(effective, expires)
    service._validate_time_range("2098-01-01Z", "2099-01-01Z")


async def test_rollback_accepts_only_same_document_previous_version(
    knowledge, monkeypatch
):
    _, catalog, version = knowledge
    catalog.get_version = AsyncMock(return_value=version)
    with pytest.raises(LookupError):
        await service.activate_document_version("other", "v1")
    with pytest.raises(ValueError):
        await service.activate_document_version("doc", "v1")
    version["status"] = "archived"
    catalog.previous_version = AsyncMock(return_value=None)
    with pytest.raises(LookupError):
        await service.activate_document_version("doc", "v1")
    catalog.previous_version.return_value = version
    catalog.activate = AsyncMock(return_value=version)
    monkeypatch.setattr(service, "_prune_versions_before_activation", AsyncMock())
    assert (await service.activate_document_version("doc", "v1"))["version_id"] == "v1"
    catalog.list_versions = AsyncMock(return_value=[version])
    assert await service.list_document_versions("doc") == [version]


async def test_large_document_list_never_initializes_vector_store(
    knowledge, monkeypatch
):
    _, catalog, version = knowledge
    catalog.list_active_and_expired.return_value = [
        {**version, "document_id": f"doc-{index}", "chunk_count": index + 1}
        for index in range(94)
    ]
    vector_store = MagicMock(side_effect=AssertionError("列表不应连接向量库"))
    monkeypatch.setattr(service, "MedicalKnowledgeBase", vector_store)
    documents = await service.list_all_documents()
    assert documents.total == 94
    assert documents.documents[-1].chunk_count == 94
    catalog.list_active_and_expired.assert_awaited_once()
    vector_store.assert_not_called()
