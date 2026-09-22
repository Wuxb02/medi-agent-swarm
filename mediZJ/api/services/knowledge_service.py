"""知识库服务：封装 MedicalKnowledgeBase 搜索"""
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from loguru import logger

from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.api.models.knowledge import (
    KnowledgeItem, KnowledgeTypeInfo,
    DocumentSummary, DocumentListResponse,
    ChunkDetail, DocumentChunksResponse,
    DocumentUploadResponse, DocumentDeleteResponse,
)


# 知识库类型定义
KNOWLEDGE_TYPES = [
    KnowledgeTypeInfo(
        key="lifestyle",
        label="生活方式",
        description="饮食、运动、睡眠、用药等生活方式建议"
    ),
    KnowledgeTypeInfo(
        key="symptoms",
        label="症状处理",
        description="急症症状识别与处理指南"
    ),
    KnowledgeTypeInfo(
        key="disease_classification",
        label="疾病编码",
        description="ICD-10 疾病分类与编码"
    ),
    KnowledgeTypeInfo(
        key="clinical_guideline",
        label="临床指南",
        description="临床诊疗指南和专家共识"
    ),
]


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_time_range(
    effective_at: Optional[str],
    expires_at: Optional[str],
) -> None:
    """校验生效/失效时间范围，时间须为 ISO 8601 格式。"""
    if effective_at and expires_at:
        try:
            eff = _parse_iso(effective_at)
            exp = _parse_iso(expires_at)
        except ValueError as exc:
            raise ValueError("时间格式无效，需为 ISO 8601 格式") from exc
        if exp <= eff:
            raise ValueError("失效时间必须晚于生效时间")
    if expires_at:
        try:
            exp = _parse_iso(expires_at)
        except ValueError as exc:
            raise ValueError("时间格式无效，需为 ISO 8601 格式") from exc
        if exp <= datetime.now(timezone.utc):
            raise ValueError("失效时间必须晚于当前时间")


def search_knowledge(
    query: str,
    top_k: int = 5,
    filter_type: Optional[str] = None
) -> List[KnowledgeItem]:
    """搜索知识库"""
    try:
        kb = MedicalKnowledgeBase()
        catalog = _catalog_with_legacy(kb)
        results = kb.search(
            query=query,
            top_k=max(top_k * 4, 20),
            filter_type=filter_type
        )
        active_results = []
        for result in results:
            metadata = result.get("metadata", {})
            document_id = metadata.get("doc_id", "")
            version_id = metadata.get("version_id") or metadata.get(
                "physical_doc_id", document_id
            )
            version = catalog.active_by_version(version_id)
            if not version:
                continue
            metadata.update(_version_metadata(version))
            active_results.append(
                KnowledgeItem(
                    id=str(result.get("id", "")),
                    content=result.get("content", ""),
                    metadata=metadata,
                    score=result.get("score", 0.0),
                )
            )
            if len(active_results) >= top_k:
                break
        return active_results
    except Exception as e:
        logger.error(f"Knowledge search error: {e}")
        return []


def get_knowledge_types() -> List[KnowledgeTypeInfo]:
    """获取知识库类型列表"""
    return KNOWLEDGE_TYPES


def get_knowledge_base_size() -> int:
    """获取知识库文档数量"""
    try:
        return len(_catalog_with_legacy(MedicalKnowledgeBase()).list_active())
    except Exception:
        return 0


def list_all_documents() -> DocumentListResponse:
    """获取知识库文档列表（含当前有效与已过期）。"""
    kb = MedicalKnowledgeBase()
    catalog = _catalog_with_legacy(kb)
    summaries = []
    for version in catalog.list_active_and_expired():
        chunks = kb.get_document_chunks(version["version_id"])
        summaries.append(
            DocumentSummary(
                doc_id=version["document_id"],
                filename=version["filename"],
                type=version["doc_type"],
                disease=version["disease"],
                source=version["source"],
                chunk_count=len(chunks),
                version_id=version["version_id"],
                document_version=str(version["version"]),
                status=version["status"],
                effective_at=version["effective_at"],
                expires_at=version["expires_at"],
            )
        )
    return DocumentListResponse(documents=summaries, total=len(summaries))


def get_document_chunks(doc_id: str) -> DocumentChunksResponse:
    """获取文档的所有分块"""
    kb = MedicalKnowledgeBase()
    version = _catalog_with_legacy(kb).active_version(doc_id)
    chunks = kb.get_document_chunks(version["version_id"]) if version else []
    details = [ChunkDetail(**c) for c in chunks]
    return DocumentChunksResponse(doc_id=doc_id, chunks=details, total=len(details))


def delete_document(doc_id: str) -> DocumentDeleteResponse:
    """彻底删除逻辑文档及其全部物理版本。"""
    kb = MedicalKnowledgeBase()
    catalog = _catalog_with_legacy(kb)
    versions = catalog.document_versions(doc_id)
    if not versions:
        raise LookupError(f"Document not found: {doc_id}")

    count = 0
    for version in versions:
        count += kb.delete_document(version["version_id"])
    catalog.delete_document_records(doc_id)

    from mediZJ.memory.lineage import MemoryLineageStore

    MemoryLineageStore().invalidate_document(doc_id, "document_deleted")
    return DocumentDeleteResponse(
        doc_id=doc_id, chunks_deleted=count, message="deleted"
    )


def upload_document(
    filename: str,
    content: str,
    doc_type: str = "general",
    disease: str = "",
    source: str = "用户上传",
    effective_at: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> DocumentUploadResponse:
    """上传文档到知识库"""
    _validate_time_range(effective_at, expires_at)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    safe_name = re.sub(r'[^\w]', '_', Path(filename).stem)
    doc_id = f"{doc_type}_{safe_name}"

    metadata = {
        "type": doc_type,
        "disease": disease or safe_name,
        "source": source,
        "filename": filename,
        "content_hash": content_hash,
        "authority_level": "authoritative" if doc_type == "clinical_guideline" else "user",
        "effective_at": effective_at,
        "expires_at": expires_at,
    }
    chunks_added, version = _ingest_version(doc_id, content, metadata)

    return DocumentUploadResponse(
        doc_id=doc_id,
        filename=filename,
        type=doc_type,
        chunks_added=chunks_added,
        version_id=version["version_id"],
        document_version=str(version["version"]),
    )


def update_document(
    doc_id: str,
    content: str,
    doc_type: Optional[str] = None,
    disease: Optional[str] = None,
    source: Optional[str] = None,
    effective_at: Optional[str] = None,
    expires_at: Optional[str] = None,
) -> DocumentUploadResponse:
    """更新知识库文档"""
    _validate_time_range(effective_at, expires_at)
    kb = MedicalKnowledgeBase()
    catalog = _catalog_with_legacy(kb)
    active = catalog.active_version(doc_id)
    if not active:
        raise ValueError(f"Document not found: {doc_id}")

    metadata = {
        "type": doc_type or active["doc_type"],
        "disease": disease or active["disease"],
        "source": source or active["source"],
        "filename": active["filename"],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "authority_level": active["authority_level"],
        "effective_at": effective_at if effective_at is not None else active["effective_at"],
        "expires_at": expires_at if expires_at is not None else active["expires_at"],
    }
    chunks_added, version = _ingest_version(doc_id, content, metadata)
    return DocumentUploadResponse(
        doc_id=doc_id,
        filename=metadata["filename"],
        type=metadata["type"],
        chunks_added=chunks_added,
        message="updated",
        version_id=version["version_id"],
        document_version=str(version["version"]),
    )


def list_document_versions(doc_id: str) -> list[dict]:
    """列出文档版本链。"""
    return _catalog_with_legacy(MedicalKnowledgeBase()).list_versions(doc_id)


def activate_document_version(doc_id: str, version_id: str) -> dict:
    """回滚到当前文档的唯一上一版。"""
    kb = MedicalKnowledgeBase()
    catalog = _catalog_with_legacy(kb)
    version = catalog.get_version(version_id)
    if not version or version["document_id"] != doc_id:
        raise LookupError("文档版本不存在")
    if version["status"] != "archived":
        raise ValueError("只能回滚到上一版")
    previous = catalog.previous_version(doc_id)
    if not previous or previous["version_id"] != version_id:
        raise LookupError("上一版不存在")
    _prune_versions_before_activation(kb, catalog, doc_id, version_id)
    return catalog.activate(version_id)


def _ingest_version(
    document_id: str,
    content: str,
    metadata: dict,
) -> tuple[int, dict]:
    kb = MedicalKnowledgeBase()
    catalog = _catalog_with_legacy(kb)
    pending = catalog.begin_version(document_id, metadata["content_hash"], metadata)
    version_metadata = {
        **metadata,
        "document_id": document_id,
        "version_id": pending["version_id"],
        "document_version": pending["version"],
    }
    try:
        chunks_added = kb.add_documents(
            [{"id": pending["version_id"], "content": content, "metadata": version_metadata}]
        )
        _prune_versions_before_activation(
            kb,
            catalog,
            document_id,
            pending["version_id"],
        )
        active = catalog.activate(pending["version_id"])
        if pending.get("supersedes_version_id"):
            from mediZJ.memory.lineage import MemoryLineageStore

            MemoryLineageStore().invalidate_document(
                document_id, "document_superseded"
            )
    except Exception as exc:
        kb.delete_document(pending["version_id"])
        catalog.mark_failed(pending["version_id"], str(exc))
        raise
    return chunks_added, active


def _prune_versions_before_activation(
    kb: MedicalKnowledgeBase,
    catalog: KnowledgeCatalog,
    document_id: str,
    target_version_id: str,
) -> None:
    """切换前删除 Active 和目标版本以外的旧 chunk。"""
    active = catalog.active_version(document_id)
    keep_ids = {target_version_id}
    if active:
        keep_ids.add(active["version_id"])
    for version in catalog.versions_to_prune(document_id, keep_ids):
        kb.delete_document(version["version_id"])
        if not catalog.delete_version_record(version["version_id"]):
            raise RuntimeError("旧知识版本目录清理失败")


def _catalog_with_legacy(kb: MedicalKnowledgeBase) -> KnowledgeCatalog:
    catalog = KnowledgeCatalog()
    for document in kb.list_documents():
        catalog.register_legacy(document)
    for version in catalog.retention_cleanup_candidates():
        kb.delete_document(version["version_id"])
        if not catalog.delete_version_record(version["version_id"]):
            raise RuntimeError("旧知识版本迁移失败")
    return catalog


def _version_metadata(version: dict) -> dict:
    return {
        "doc_id": version["document_id"],
        "document_id": version["document_id"],
        "version_id": version["version_id"],
        "document_version": str(version["version"]),
        "effective_at": version["effective_at"],
        "authority_level": version["authority_level"],
    }
