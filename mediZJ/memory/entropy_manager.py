"""
记忆系统熵管理器
自动清理冗余、过时的记忆

基于 Harness Engineering 原则：
- 系统复杂度的"垃圾回收"
- 自动去重和压缩（基于向量语义相似度）
- 保持系统简洁
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from loguru import logger
import numpy as np

from mediZJ.core.prompt_loader import PromptLoader
from .embedding import batch_cosine_similarity


class MemoryEntropyManager:
    """记忆熵管理器"""

    def __init__(self, embedding_client, llm_client=None):
        """
        初始化熵管理器

        Args:
            embedding_client: SentenceTransformer 实例，用于语义相似度计算
            llm_client: LLM 客户端实例（可选），用于生成语义摘要。
                        为 None 时降级为截断模式。
        """
        self.embedding_client = embedding_client
        self.llm_client = llm_client
        self.deduplication_threshold = 0.9  # 语义相似度阈值
        self.max_age_days = 90  # 记忆最大保留天数
        self.compression_threshold = 10  # 超过10条消息开始压缩

        mode = "LLM语义摘要" if llm_client else "截断降级"
        logger.debug(f"📦 MemoryEntropyManager initialized (压缩模式: {mode})")

    def _encode_contents(self, contents: List[str]) -> np.ndarray:
        """批量编码文本为向量"""
        return self.embedding_client.encode(contents, show_progress_bar=False)

    def _greedy_dedup(
        self,
        items: List[Dict[str, Any]],
        contents: List[str],
    ) -> List[Dict[str, Any]]:
        """
        基于向量相似度的贪心去重。

        计算完整相似度矩阵后，逐条与已保留条目比较，相似度 > threshold 则跳过。

        Args:
            items: 原始条目列表
            contents: 对应的文本内容列表（用于编码）

        Returns:
            去重后的条目列表
        """
        if not items:
            return []

        vectors = self._encode_contents(contents)
        sim_matrix = batch_cosine_similarity(vectors)

        unique_items = []
        kept_indices = []

        for i, item in enumerate(items):
            is_duplicate = False
            for j in kept_indices:
                sim = float(sim_matrix[i, j])
                if sim > self.deduplication_threshold:
                    is_duplicate = True
                    logger.debug(
                        f"🗑️ Deduplicated (sim={sim:.2f}): {contents[i][:30]}..."
                    )
                    break

            if not is_duplicate:
                unique_items.append(item)
                kept_indices.append(i)

        removed_count = len(items) - len(unique_items)
        if removed_count > 0:
            logger.info(f"🗑️ Removed {removed_count} duplicate entries")

        return unique_items

    def deduplicate_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        去重消息列表（基于语义相似度）

        Args:
            messages: 消息列表

        Returns:
            去重后的消息列表
        """
        if not messages:
            return []

        contents = [msg.get("content", "") for msg in messages]
        return self._greedy_dedup(messages, contents)

    def deduplicate_sessions(self, sessions: List[Dict[str, Any]]) -> List[Dict]:
        """
        去重相似会话（基于语义相似度）

        Args:
            sessions: 会话列表

        Returns:
            去重后的会话列表
        """
        if not sessions:
            return []

        contents = [
            f"{s.get('question', '')}:{s.get('summary', '')}"
            for s in sessions
        ]
        return self._greedy_dedup(sessions, contents)

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

    async def compress_session_history(
        self,
        messages: List[Dict],
        max_messages: int = 10,
        entropy: Optional[Dict[str, Any]] = None
    ) -> List[Dict]:
        """
        压缩会话历史

        策略：
        1. 保留最近的 max_messages 条消息
        2. 对更早的消息进行摘要压缩

        Args:
            messages: 消息列表
            max_messages: 保留的最大消息数
            entropy: 熵估算结果（可选），用于动态约束 LLM 摘要

        Returns:
            压缩后的消息列表
        """
        if len(messages) <= max_messages:
            return messages

        # 保留最近的消息
        recent = messages[-max_messages:]

        # 压缩更早的消息
        older = messages[:-max_messages]
        compressed = await self._compress_older_messages(older, entropy=entropy)

        logger.info(
            f"📦 Compressed {len(older)} messages to {len(compressed)} summaries"
        )

        return compressed + recent

    async def _compress_older_messages(
        self, messages: List[Dict], entropy: Optional[Dict[str, Any]] = None
    ) -> List[Dict]:
        """
        压缩更早的消息

        优先使用 LLM 生成语义摘要，失败或无 llm_client 时降级为截断。

        Args:
            messages: 消息列表
            entropy: 熵估算结果（可选），用于动态约束 LLM 摘要

        Returns:
            压缩后的摘要列表
        """
        if self.llm_client:
            try:
                return await self._compress_with_llm(messages, entropy=entropy)
            except Exception as e:
                logger.warning(f"LLM 压缩失败，降级为截断模式: {e}")
        return self._compress_by_truncation(messages)

    async def _compress_with_llm(
        self, messages: List[Dict], entropy: Optional[Dict[str, Any]] = None
    ) -> List[Dict]:
        """
        使用 LLM 生成语义摘要

        Args:
            messages: 待压缩的消息列表
            entropy: 熵估算结果（可选），用于注入动态约束到 prompt

        Returns:
            包含单条语义摘要的列表
        """
        # 拼装对话文本
        dialogue_lines = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                dialogue_lines.append(f"用户: {content}")
            elif role == "assistant":
                dialogue_lines.append(f"助手: {content}")
            # 跳过 system/tool 消息

        if not dialogue_lines:
            return self._compress_by_truncation(messages)

        dialogue_text = "\n".join(dialogue_lines)

        # 根据熵指标构造额外约束
        base_system = PromptLoader.load("memory/compression_system.j2")
        if entropy:
            constraints = []
            if entropy.get("duplicate_rate", 0) > 0.2:
                constraints.append("对话中存在大量重复内容，请高度去重，相似问题只保留一个")
            if entropy.get("avg_message_length", 0) > 500:
                constraints.append("单条消息较长，请重点提炼核心信息")
            if entropy.get("total_messages", 0) > 30:
                constraints.append("对话轮次较多，请高度概括，聚焦最后的关键结论")

            if constraints:
                constraint_text = "额外要求：\n" + "\n".join(f"- {c}" for c in constraints)
                system_content = f"{base_system}\n\n{constraint_text}"
            else:
                system_content = base_system
        else:
            system_content = base_system

        # 动态 max_tokens
        max_tokens = 512 if entropy and entropy.get("entropy_level") == "high" else 256

        prompt_messages = [
            {
                "role": "system",
                "content": system_content,
            },
            {
                "role": "user",
                "content": PromptLoader.render(
                    "memory/compression_user.j2",
                    dialogue_text=dialogue_text,
                ),
            },
        ]

        summary_text = await self.llm_client.chat(
            messages=prompt_messages,
            temperature=0.3,
            max_tokens=max_tokens,
        )

        logger.debug(f"📝 LLM 生成摘要: {summary_text[:80]}...")

        return [{"role": "system", "content": f"[语义摘要] {summary_text}"}]

    def _compress_by_truncation(self, messages: List[Dict]) -> List[Dict]:
        """
        截断式压缩（降级方案）

        每两条（user + assistant）压缩为一条截断摘要。

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
        - 重复率（基于语义相似度）
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
                "unique_messages": 0,
                "estimated_duplicates": 0,
                "duplicate_rate": 0,
                "avg_message_length": 0,
                "entropy_level": "low",
                "recommendations": []
            }

        # 统计
        total_messages = len(messages)
        total_length = sum(len(msg.get("content", "")) for msg in messages)
        avg_length = total_length / total_messages if total_messages > 0 else 0

        # 估算重复率（基于向量语义相似度）
        contents = [msg.get("content", "") for msg in messages]
        duplicate_rate = self._estimate_duplicate_rate(contents)

        unique_count = int(total_messages * (1 - duplicate_rate))
        estimated_duplicates = total_messages - unique_count

        # 评估熵等级（多指标，任一触发即 high）
        entropy_level = "low"
        recommendations = []

        if total_messages > 20:
            entropy_level = "high"
            recommendations.append("建议压缩历史消息（当前 > 20 条）")

        if duplicate_rate > 0.15:
            entropy_level = "high"
            recommendations.append(f"检测到 {duplicate_rate:.1%} 重复消息，建议去重")

        if avg_length > 500:
            entropy_level = "high"
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

    def _estimate_duplicate_rate(self, contents: List[str]) -> float:
        """
        基于向量相似度估算重复率。

        计算 pairwise 余弦相似度，相似度 > threshold 的对数占总对数的比例。

        Args:
            contents: 文本内容列表

        Returns:
            重复率（0.0 ~ 1.0）
        """
        n = len(contents)
        if n <= 1:
            return 0.0

        vectors = self._encode_contents(contents)
        sim_matrix = batch_cosine_similarity(vectors)

        # 统计上三角中 > threshold 的对数（排除对角线）
        total_pairs = n * (n - 1) / 2
        upper_tri = np.triu(sim_matrix, k=1)
        duplicate_pairs = int(np.sum(upper_tri > self.deduplication_threshold))

        return duplicate_pairs / total_pairs if total_pairs > 0 else 0.0

    async def auto_clean(
        self,
        messages: List[Dict],
        max_messages: int = 10
    ) -> List[Dict]:
        """
        自动清理（一键式，熵驱动）

        流程：
        1. 熵估算 → low 直接返回
        2. 去重 → 复检 → low 则跳过 LLM
        3. 压缩（将熵指标注入 LLM 约束）

        Args:
            messages: 消息列表
            max_messages: 压缩后保留的最大消息数

        Returns:
            清理后的消息列表
        """
        if not messages:
            return messages

        # 1. 熵估算
        entropy = self.estimate_entropy(messages)

        # 2. 低熵不清理
        if entropy["entropy_level"] == "low":
            return messages

        # 3. 去重（重复率 > 0.1 才执行）
        cleaned = messages
        if entropy["duplicate_rate"] > 0.1:
            cleaned = self.deduplicate_messages(cleaned)
            # 去重后复检
            recheck_entropy = self.estimate_entropy(cleaned)
            if recheck_entropy["entropy_level"] == "low":
                return cleaned

        # 4. 压缩
        if len(cleaned) > max_messages:
            cleaned = await self.compress_session_history(
                cleaned, max_messages, entropy=entropy
            )

        return cleaned
