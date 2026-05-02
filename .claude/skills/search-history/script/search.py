"""
Search Conversation History Skill
搜索当前会话历史（短期记忆）
"""
from typing import Dict, Any
from loguru import logger


async def search_history(session_id: str, limit: int = 10) -> Dict[str, Any]:
    """
    搜索当前会话的历史对话

    Args:
        session_id: 会话ID
        limit: 最多返回多少轮对话（默认10轮）

    Returns:
        {
            "answer": "格式化的历史对话",
            "total_messages": 总消息数,
            "session_id": "会话ID"
        }
    """
    logger.info(f"Searching conversation history: session={session_id}, limit={limit}")

    try:
        # 导入记忆系统
        from memory.short_term import ShortTermMemory

        # 获取短期记忆实例
        memory = ShortTermMemory(storage_type="memory")

        # 获取历史消息
        messages = memory.get_recent_messages(session_id, limit=limit * 2)  # 每轮包含user+assistant

        if not messages:
            return {
                "answer": "当前会话还没有历史记录。这是您的第一次提问。",
                "total_messages": 0,
                "session_id": session_id
            }

        # 格式化输出
        formatted_history = format_history(messages)

        return {
            "answer": formatted_history,
            "total_messages": len(messages),
            "session_id": session_id
        }

    except Exception as e:
        logger.error(f"Failed to search history: {e}")
        return {
            "answer": f"抱歉，无法检索历史对话：{str(e)}",
            "total_messages": 0,
            "session_id": session_id
        }


def format_history(messages: list) -> str:
    """
    格式化历史对话

    Args:
        messages: 消息列表

    Returns:
        格式化的字符串
    """
    if not messages:
        return "无历史记录"

    output = ["【当前会话历史】\n"]

    # 按角色分组显示
    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp", "")

        if role == "user":
            output.append(f"【轮次 {i // 2 + 1}】")
            output.append(f"用户: {content}")
        elif role == "assistant":
            output.append(f"系统: {content[:200]}..." if len(content) > 200 else f"系统: {content}")
            output.append("")  # 空行分隔

    return "\n".join(output)


# 同步版本
def search_history_sync(session_id: str, limit: int = 10) -> Dict[str, Any]:
    """同步版本的搜索历史"""
    import asyncio
    return asyncio.run(search_history(session_id, limit))


if __name__ == "__main__":
    # 测试
    import asyncio

    test_session_id = "test_session_123"
    result = asyncio.run(search_history(test_session_id))

    print("=" * 70)
    print(f"会话ID: {test_session_id}")
    print("=" * 70)
    print(result["answer"])
    print("=" * 70)
    print(f"总消息数: {result['total_messages']}")
