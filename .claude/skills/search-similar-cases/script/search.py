"""
Search Similar Cases Skill
搜索相似历史案例（长期记忆/Mem0）
"""
from typing import Dict, Any
from loguru import logger


async def search_similar_cases(query: str, max_results: int = 3) -> Dict[str, Any]:
    """
    搜索相似的历史案例

    Args:
        query: 查询内容
        max_results: 最多返回结果数（默认3）

    Returns:
        {
            "answer": "格式化的相似案例",
            "total_found": 找到的案例数,
            "query": "原始查询"
        }
    """
    logger.info(f"Searching similar cases: query={query}, max_results={max_results}")

    try:
        # 导入长期记忆系统
        from memory.long_term import LongTermMemory

        # 获取长期记忆实例
        memory = LongTermMemory()

        if not memory.enabled:
            return {
                "answer": "长期记忆功能未启用。无法搜索历史案例。",
                "total_found": 0,
                "query": query
            }

        # 搜索相似会话
        results = memory.search_similar_sessions(query=query, limit=max_results)

        if not results:
            return {
                "answer": f"未找到与'{query}'相关的历史案例。",
                "total_found": 0,
                "query": query
            }

        # 格式化输出
        formatted_cases = format_cases(results)

        return {
            "answer": formatted_cases,
            "total_found": len(results),
            "query": query
        }

    except Exception as e:
        logger.error(f"Failed to search similar cases: {e}")
        return {
            "answer": f"抱歉，搜索历史案例时出错：{str(e)}",
            "total_found": 0,
            "query": query
        }


def format_cases(results: list) -> str:
    """
    格式化相似案例

    Args:
        results: 检索结果列表

    Returns:
        格式化的字符串
    """
    if not results:
        return "未找到相似案例。"

    output = ["【相似历史案例】\n"]

    for i, case in enumerate(results, 1):
        content = case.get("content", "")
        score = case.get("score", 0.0)
        metadata = case.get("metadata", {})

        output.append(f"【案例 {i}】（相似度: {score:.2%}）")
        output.append(content[:300] + "..." if len(content) > 300 else content)

        # 显示时间戳（如果有）
        timestamp = metadata.get("timestamp", "")
        if timestamp:
            output.append(f"时间: {timestamp}")

        output.append("")  # 空行分隔

    return "\n".join(output)


# 同步版本
def search_similar_cases_sync(query: str, max_results: int = 3) -> Dict[str, Any]:
    """同步版本的搜索相似案例"""
    import asyncio
    return asyncio.run(search_similar_cases(query, max_results))


if __name__ == "__main__":
    # 测试
    import asyncio

    test_query = "高血压患者的生活方式建议"
    result = asyncio.run(search_similar_cases(test_query))

    print("=" * 70)
    print(f"查询: {test_query}")
    print("=" * 70)
    print(result["answer"])
    print("=" * 70)
    print(f"找到案例数: {result['total_found']}")
