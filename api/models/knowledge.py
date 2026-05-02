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
