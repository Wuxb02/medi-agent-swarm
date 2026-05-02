"""
Recommend Lifestyle Skill
生活方式建议 Skill（自包含，无需依赖tools）
"""
from typing import Dict, Any
from loguru import logger

# 全局知识库实例
_kb_instance = None

def get_knowledge_base():
    global _kb_instance
    if _kb_instance is None:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        _kb_instance = MedicalKnowledgeBase()
    return _kb_instance


async def recommend_lifestyle(diagnosis: str) -> Dict[str, Any]:
    """
    提供生活方式建议

    Args:
        diagnosis: 疾病名称或症状

    Returns:
        {
            "answer": "格式化的生活方式建议",
            "diagnosis": "疾病名称",
            "categories": ["diet", "exercise", "lifestyle", "medication"]
        }
    """
    logger.info(f"Recommending lifestyle for: {diagnosis}")

    # 使用知识库单例
    kb = get_knowledge_base()

    # 从 Milvus 检索生活方式建议
    results = kb.search(
        query=f"{diagnosis} 生活方式建议 饮食 运动 用药",
        top_k=1,
        filter_type="lifestyle"
    )

    if results and results[0]["score"] > 0.1:
        doc = results[0]
        content = doc["content"]

        return {
            "answer": format_advice(diagnosis, content),
            "diagnosis": diagnosis,
            "categories": ["diet", "exercise", "lifestyle", "medication"],
            "source": "向量数据库"
        }
    else:
        # 未找到相关内容
        logger.warning(f"No lifestyle advice found in vector DB for {diagnosis}")
        return {
            "answer": f"未找到关于'{diagnosis}'的生活方式建议，请尝试更具体的疾病名称或联系医生咨询。",
            "diagnosis": diagnosis,
            "categories": [],
            "source": "未找到"
        }


def format_advice(diagnosis: str, content: str) -> str:
    """格式化生活方式建议"""
    output = [
        f"【{diagnosis}生活方式建议】\n",
        content,
        "\n【免责声明】",
        "以上建议仅供参考，具体请咨询医生或营养师。"
    ]

    return "\n".join(output)


def recommend_lifestyle_sync(diagnosis: str) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(recommend_lifestyle(diagnosis))
