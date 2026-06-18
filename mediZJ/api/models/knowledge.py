"""知识库接口的请求/响应模型"""
from typing import Dict, Any, List, Optional
from pydantic import BaseModel


class KnowledgeSearchRequest(BaseModel):
    """知识库搜索请求"""
    query: str
    top_k: int = 5
    filter_type: Optional[str] = None  # lifestyle / symptoms / disease_classification / clinical_guideline


class KnowledgeItem(BaseModel):
    """知识条目"""
    id: str
    content: str
    metadata: Dict[str, Any] = {}
    score: float = 0.0


class KnowledgeSearchResponse(BaseModel):
    """知识库搜索响应"""
    results: List[KnowledgeItem] = []
    total: int = 0


class KnowledgeTypeInfo(BaseModel):
    """知识库类型信息"""
    key: str
    label: str
    description: str


class KnowledgeTypesResponse(BaseModel):
    """知识库类型列表响应"""
    types: List[KnowledgeTypeInfo] = []


class DocumentSummary(BaseModel):
    """文档摘要（列表项）"""
    doc_id: str
    filename: str
    type: str
    disease: str
    source: str
    chunk_count: int


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    documents: List[DocumentSummary] = []
    total: int = 0


class ChunkDetail(BaseModel):
    """文档块详情"""
    milvus_id: int
    chunk_id: int
    content: str
    total_chunks: int


class DocumentChunksResponse(BaseModel):
    """文档块列表响应"""
    doc_id: str
    chunks: List[ChunkDetail] = []
    total: int = 0


class DocumentUploadResponse(BaseModel):
    """文件上传响应"""
    doc_id: str
    filename: str
    type: str
    chunks_added: int
    message: str = "ok"


class DocumentDeleteResponse(BaseModel):
    """文件删除响应"""
    doc_id: str
    chunks_deleted: int
    message: str = "ok"


class DocumentUpdateRequest(BaseModel):
    """文档更新请求"""
    content: str
    type: Optional[str] = None
    disease: Optional[str] = None
    source: Optional[str] = None
