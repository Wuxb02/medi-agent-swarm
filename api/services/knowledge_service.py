"""知识库服务：封装 MedicalKnowledgeBase 搜索"""
from typing import List, Optional
from loguru import logger

from knowledge.milvus_kb import MedicalKnowledgeBase
from api.models.knowledge import KnowledgeItem, KnowledgeTypeInfo


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
        return kb.collection.num_entities if kb.collection else 0
    except Exception:
        return 0
