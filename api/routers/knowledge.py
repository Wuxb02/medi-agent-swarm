"""知识库路由"""
from fastapi import APIRouter

from api.models.knowledge import (
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeTypesResponse,
)
from api.services.knowledge_service import search_knowledge, get_knowledge_types

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search(request: KnowledgeSearchRequest):
    """搜索知识库"""
    results = search_knowledge(
        query=request.query,
        top_k=request.top_k,
        filter_type=request.filter_type
    )
    return KnowledgeSearchResponse(results=results, total=len(results))


@router.get("/types", response_model=KnowledgeTypesResponse)
async def get_types():
    """获取知识库类型列表"""
    types = get_knowledge_types()
    return KnowledgeTypesResponse(types=types)
