"""
记忆系统熵管理器
自动清理冗余、过时的记忆

基于 Harness Engineering 原则：
- 系统复杂度的"垃圾回收"
- 自动去重和压缩
- 保持系统简洁
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from loguru import logger
import hashlib
import json


class MemoryEntropyManager:
    """记忆熵管理器"""

    def __init__(self):
        """初始化熵管理器"""
        self.deduplication_threshold = 0.9  # 相似度阈值
        self.max_age_days = 90  # 记忆最大保留天数
        self.compression_threshold = 10  # 超过10条消息开始压缩

        logger.debug("📦 MemoryEntropyManager initialized")

    def deduplicate_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        去重消息列表

        使用内容哈希检测完全重复的消息
        保留最新的一条

        Args:
            messages: 消息列表

        Returns:
            去重后的消息列表
        """
        if not messages:
            return []

        unique_messages = []
        seen_hashes = {}

        for msg in messages:
            content = msg.get("content", "")
            role = msg.get("role", "")

            # 生成内容哈希
            content_hash = hashlib.md5(f"{role}:{content}".encode()).hexdigest()

            if content_hash not in seen_hashes:
                unique_messages.append(msg)
                seen_hashes[content_hash] = msg
            else:
                logger.debug(f"🗑️ Deduplicated message: {content[:30]}...")

        removed_count = len(messages) - len(unique_messages)
        if removed_count > 0:
            logger.info(f"🗑️ Removed {removed_count} duplicate messages")

        return unique_messages

    def deduplicate_sessions(self, sessions: List[Dict[str, Any]]) -> List[Dict]:
        """
        去重相似会话

        使用内容哈希检测完全重复的会话总结
        使用向量相似度检测高度相似的会话（未实现）

        Args:
            sessions: 会话列表

        Returns:
            去重后的会话列表
        """
        if not sessions:
            return []

        unique_sessions = []
        seen_hashes = set()

        for session in sessions:
            # 提取关键内容
            question = session.get("question", "")
            summary = session.get("summary", "")
            content = f"{question}:{summary}"

            # 生成哈希
            content_hash = hashlib.md5(content.encode()).hexdigest()

            if content_hash not in seen_hashes:
                unique_sessions.append(session)
                seen_hashes.add(content_hash)
            else:
                logger.debug(
                    f"🗑️ Deduplicated session: {session.get('session_id', 'unknown')}"
                )

        removed_count = len(sessions) - len(unique_sessions)
        if removed_count > 0:
            logger.info(f"🗑️ Removed {removed_count} duplicate sessions")

        return unique_sessions

    def cleanup_old_memories(
        self,
        memories: List[Dict],
        max_age_days: Optional[int] = None
    ) -> List[Dict]:
        """
        清理过期记忆

        Args:
            memories: 记忆列表
            max_age_days: 最大保留天数（默认90天）

        Returns:
            清理后的记忆列表
        """
        if not memories:
            return []

        max_age_days = max_age_days or self.max_age_days
        cutoff_date = datetime.now() - timedelta(days=max_age_days)

        cleaned = []
        removed_count = 0

        for memory in memories:
            # 提取时间戳（支持多种格式）
            timestamp = None
            if "timestamp" in memory:
                timestamp_value = memory["timestamp"]
                if isinstance(timestamp_value, datetime):
                    timestamp = timestamp_value
                elif isinstance(timestamp_value, str):
                    try:
                        timestamp = datetime.fromisoformat(timestamp_value)
                    except ValueError:
                        logger.warning(f"Invalid timestamp format: {timestamp_value}")

            # 保留最近的记忆
            if timestamp and timestamp > cutoff_date:
                cleaned.append(memory)
            elif not timestamp:
                # 如果没有时间戳，保留（避免误删）
                cleaned.append(memory)
                logger.warning(f"Memory without timestamp: {memory.get('session_id', 'unknown')}")
            else:
                removed_count += 1

        if removed_count > 0:
            logger.info(f"🗑️ Cleaned up {removed_count} old memories (>{max_age_days} days)")

        return cleaned

    def compress_session_history(
        self,
        messages: List[Dict],
        max_messages: int = 10
    ) -> List[Dict]:
        """
        压缩会话历史

        策略：
        1. 保留最近的 max_messages 条消息
        2. 对更早的消息进行摘要压缩

        Args:
            messages: 消息列表
            max_messages: 保留的最大消息数

        Returns:
            压缩后的消息列表
        """
        if len(messages) <= max_messages:
            return messages

        # 保留最近的消息
        recent = messages[-max_messages:]

        # 压缩更早的消息
        older = messages[:-max_messages]
        compressed = self._compress_older_messages(older)

        logger.info(
            f"📦 Compressed {len(older)} messages to {len(compressed)} summaries"
        )

        return compressed + recent

    def _compress_older_messages(self, messages: List[Dict]) -> List[Dict]:
        """
        压缩更早的消息

        简化实现：每两条（user + assistant）压缩为一条摘要

        Args:
            messages: 消息列表

        Returns:
            压缩后的摘要列表
        """
        compressed = []

        i = 0
        while i < len(messages):
            # 查找 user 和 assistant 配对
            user_msg = None
            assistant_msg = None

            # 查找 user 消息
            while i < len(messages) and messages[i].get("role") != "user":
                i += 1

            if i < len(messages):
                user_msg = messages[i]
                i += 1

            # 查找对应的 assistant 消息
            while i < len(messages) and messages[i].get("role") != "assistant":
                i += 1

            if i < len(messages):
                assistant_msg = messages[i]
                i += 1

            # 生成摘要
            if user_msg and assistant_msg:
                user_content = user_msg.get("content", "")
                assistant_content = assistant_msg.get("content", "")

                summary = f"[历史摘要] 用户问: {user_content[:50]}... 回答: {assistant_content[:100]}..."

                compressed.append({
                    "role": "system",
                    "content": summary
                })

        return compressed

    def estimate_entropy(self, messages: List[Dict]) -> Dict[str, Any]:
        """
        估算消息历史的熵（复杂度）

        指标：
        - 消息总数
        - 重复率（近似）
        - 平均消息长度
        - 建议操作

        Args:
            messages: 消息列表

        Returns:
            熵估算结果
        """
        if not messages:
            return {
                "total_messages": 0,
                "estimated_duplicates": 0,
                "avg_message_length": 0,
                "entropy_level": "low",
                "recommendations": []
            }

        # 统计
        total_messages = len(messages)
        total_length = sum(len(msg.get("content", "")) for msg in messages)
        avg_length = total_length / total_messages if total_messages > 0 else 0

        # 估算重复率（简单版本：基于内容哈希）
        content_hashes = [
            hashlib.md5(msg.get("content", "").encode()).hexdigest()
            for msg in messages
        ]
        unique_count = len(set(content_hashes))
        estimated_duplicates = total_messages - unique_count
        duplicate_rate = estimated_duplicates / total_messages if total_messages > 0 else 0

        # 评估熵等级
        entropy_level = "low"
        recommendations = []

        if total_messages > 50:
            entropy_level = "high"
            recommendations.append("建议压缩历史消息（当前 > 50 条）")
        elif total_messages > 20:
            entropy_level = "medium"
            recommendations.append("考虑压缩历史消息（当前 > 20 条）")

        if duplicate_rate > 0.2:
            recommendations.append(f"检测到 {duplicate_rate:.1%} 重复消息，建议去重")

        if avg_length > 1000:
            recommendations.append(f"平均消息长度较大（{avg_length:.0f}字），考虑摘要")

        return {
            "total_messages": total_messages,
            "unique_messages": unique_count,
            "estimated_duplicates": estimated_duplicates,
            "duplicate_rate": duplicate_rate,
            "avg_message_length": avg_length,
            "entropy_level": entropy_level,
            "recommendations": recommendations
        }

    def auto_clean(
        self,
        messages: List[Dict],
        enable_deduplication: bool = True,
        enable_compression: bool = True,
        max_messages: int = 10
    ) -> List[Dict]:
        """
        自动清理（一键式）

        依次执行：去重 → 压缩

        Args:
            messages: 消息列表
            enable_deduplication: 是否启用去重
            enable_compression: 是否启用压缩
            max_messages: 压缩后保留的最大消息数

        Returns:
            清理后的消息列表
        """
        cleaned = messages

        # 去重
        if enable_deduplication:
            cleaned = self.deduplicate_messages(cleaned)

        # 压缩
        if enable_compression and len(cleaned) > max_messages:
            cleaned = self.compress_session_history(cleaned, max_messages)

        return cleaned
