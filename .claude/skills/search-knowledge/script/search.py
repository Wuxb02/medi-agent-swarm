"""
Search Knowledge Skill
搜索医学知识库 Skill（自包含，无需依赖tools）
"""
from typing import Dict, Any
from loguru import logger

# 全局知识库实例（避免重复加载模型）
_kb_instance = None


def get_knowledge_base():
    """获取知识库单例"""
    global _kb_instance
    if _kb_instance is None:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        _kb_instance = MedicalKnowledgeBase()
    return _kb_instance


async def search_knowledge(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    搜索医学知识库

    Args:
        query: 查询内容
        max_results: 最多返回结果数（默认5）

    Returns:
        {
            "answer": "格式化的知识库检索结果",
            "total_found": 检索到的结果数,
            "query": "原始查询"
        }
    """
    logger.info(f"Searching knowledge base: query={query}, max_results={max_results}")

    # 获取知识库单例（避免重复加载模型）
    kb = get_knowledge_base()

    # 使用 Milvus 进行语义检索
    results = kb.search(
        query=query,
        top_k=max_results,
        filter_type=None
    )

    # 格式化结果
    formatted_results = []
    for doc in results:
        formatted_results.append({
            "title": f"关于{doc['metadata'].get('disease', query)}的医学信息",
            "content": doc["content"],
            "source": doc["metadata"].get("source", "医学知识库"),
            "score": doc["score"],
            "type": doc["metadata"].get("type")
        })

    # Skill 的格式化输出
    if formatted_results:
        return {
            "answer": format_results(formatted_results),
            "total_found": len(formatted_results),
            "query": query
        }
    else:
        return {
            "answer": f"未找到关于'{query}'的相关医学知识，请尝试更具体的查询。",
            "total_found": 0,
            "query": query
        }


def format_results(results: list) -> str:
    """
    格式化知识库检索结果

    Args:
        results: 检索结果列表

    Returns:
        格式化的字符串
    """
    if not results:
        return "未找到相关信息。"

    output = []
    for i, doc in enumerate(results, 1):
        output.append(f"【结果 {i}】")
        output.append(doc.get("content", "无内容"))

        # 显示相关度分数（如果有）
        score = doc.get("score", 0)
        if score > 0:
            output.append(f"相关度: {score:.2%}")

        output.append("")  # 空行分隔

    return "\n".join(output)


# 同步版本（如果需要）
def search_knowledge_sync(query: str, max_results: int = 5) -> Dict[str, Any]:
    """同步版本的搜索知识库"""
    import asyncio
    return asyncio.run(search_knowledge(query, max_results))


if __name__ == "__main__":
    # 测试
    import asyncio

    test_query = "高血压的治疗方法"
    result = asyncio.run(search_knowledge(test_query))

    print("=" * 70)
    print(f"查询: {test_query}")
    print("=" * 70)
    print(result["answer"])
    print("=" * 70)
    print(f"找到结果数: {result['total_found']}")
