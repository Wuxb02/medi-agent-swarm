"""知识库服务：封装 MedicalKnowledgeBase 搜索"""
import hashlib
import json
import re
from pathlib import Path
from typing import List, Optional
from loguru import logger

from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
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


def search_knowledge(
    query: str,
    top_k: int = 5,
    filter_type: Optional[str] = None
) -> List[KnowledgeItem]:
    """搜索知识库"""
    try:
        kb = MedicalKnowledgeBase()
        results = kb.search(
            query=query,
            top_k=top_k,
            filter_type=filter_type
        )
        return [
            KnowledgeItem(
                id=str(r.get("id", "")),
                content=r.get("content", ""),
                metadata=r.get("metadata", {}),
                score=r.get("score", 0.0)
            )
            for r in results
        ]
    except Exception as e:
        logger.error(f"Knowledge search error: {e}")
        return []


def get_knowledge_types() -> List[KnowledgeTypeInfo]:
    """获取知识库类型列表"""
    return KNOWLEDGE_TYPES


def get_knowledge_base_size() -> int:
    """获取知识库文档数量"""
    try:
        kb = MedicalKnowledgeBase()
        return kb.count_documents()
    except Exception:
        return 0


def list_all_documents() -> DocumentListResponse:
    """获取知识库文档列表"""
    kb = MedicalKnowledgeBase()
    docs = kb.list_documents()
    summaries = [DocumentSummary(**d) for d in docs]
    return DocumentListResponse(documents=summaries, total=len(summaries))


def get_document_chunks(doc_id: str) -> DocumentChunksResponse:
    """获取文档的所有分块"""
    kb = MedicalKnowledgeBase()
    chunks = kb.get_document_chunks(doc_id)
    details = [ChunkDetail(**c) for c in chunks]
    return DocumentChunksResponse(doc_id=doc_id, chunks=details, total=len(details))


def delete_document(doc_id: str) -> DocumentDeleteResponse:
    """删除文档"""
    kb = MedicalKnowledgeBase()
    count = kb.delete_document(doc_id)
    return DocumentDeleteResponse(doc_id=doc_id, chunks_deleted=count)


def upload_document(
    filename: str,
    content: str,
    doc_type: str = "general",
    disease: str = "",
    source: str = "用户上传",
) -> DocumentUploadResponse:
    """上传文档到知识库"""
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    safe_name = re.sub(r'[^\w]', '_', Path(filename).stem)
    doc_id = f"{doc_type}_{safe_name}"

    kb = MedicalKnowledgeBase()

    if kb.document_exists_by_hash(content_hash):
        raise ValueError(f"内容相同的文档已存在: {filename}")

    metadata = {
        "type": doc_type,
        "disease": disease or safe_name,
        "source": source,
        "filename": filename,
        "content_hash": content_hash,
    }
    doc = {"id": doc_id, "content": content, "metadata": metadata}
    chunks_added = kb.add_documents([doc])

    return DocumentUploadResponse(
        doc_id=doc_id,
        filename=filename,
        type=doc_type,
        chunks_added=chunks_added,
    )


def update_document(
    doc_id: str,
    content: str,
    doc_type: Optional[str] = None,
    disease: Optional[str] = None,
    source: Optional[str] = None,
) -> DocumentUploadResponse:
    """更新知识库文档"""
    kb = MedicalKnowledgeBase()

    existing = kb.get_document_chunks(doc_id)
    if not existing:
        raise ValueError(f"Document not found: {doc_id}")

    # 从已有 chunk 获取原始元数据
    old_meta_row = kb.milvus_client.query(
        collection_name=kb.collection_name,
        filter=f'doc_id == "{doc_id}"',
        output_fields=["doc_type", "disease", "source", "filename"],
        limit=1
    )
    old_meta = old_meta_row[0] if old_meta_row else {}

    metadata = {
        "type": doc_type or old_meta.get("doc_type", "general"),
        "disease": disease or old_meta.get("disease", ""),
        "source": source or old_meta.get("source", "用户上传"),
        "filename": old_meta.get("filename", ""),
    }

    chunks_added = kb.update_document(doc_id, content, metadata)
    return DocumentUploadResponse(
        doc_id=doc_id,
        filename=metadata["filename"],
        type=metadata["type"],
        chunks_added=chunks_added,
        message="updated",
    )
