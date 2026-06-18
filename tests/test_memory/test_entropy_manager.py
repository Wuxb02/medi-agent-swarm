"""test_memory/test_entropy_manager.py — MemoryEntropyManager 单元测试"""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch, AsyncMock
from mediZJ.memory.entropy_manager import MemoryEntropyManager


@pytest.fixture
def mock_embedding():
    """返回假 embedding client，固定返回 512 维向量。"""
    stub = MagicMock()
    stub.encode = MagicMock(return_value=np.array([[0.1] * 512]))
    return stub


@pytest.fixture
def manager(mock_embedding):
    return MemoryEntropyManager(embedding_client=mock_embedding)


class TestDeduplicateMessages:
    def test_empty_list(self, manager):
        assert manager.deduplicate_messages([]) == []

    def test_single_message(self, manager, mock_embedding):
        msgs = [{"role": "user", "content": "hello"}]
        mock_embedding.encode.return_value = np.array([[0.1] * 512])
        result = manager.deduplicate_messages(msgs)
        assert len(result) == 1

    def test_multiple_messages_no_duplicates(self, manager, mock_embedding):
        """当相似度低于阈值时不删除。"""
        msgs = [{"role": "user", "content": "msg1"}, {"role": "user", "content": "msg2"}]
        # 模拟低相似度的向量（0.5 < 0.9 阈值）
        mock_embedding.encode.return_value = np.array([[1.0] * 512, [-1.0] * 512])
        result = manager.deduplicate_messages(msgs)
        assert len(result) == 2

    def test_high_similarity_deduplicates(self, manager, mock_embedding):
        """当相似度高于阈值时删除重复。"""
        msgs = [{"role": "user", "content": "same"}, {"role": "user", "content": "same"}]
        # 模拟高相似度（相同向量，cosine sim = 1.0 > 0.9 阈值）
        mock_embedding.encode.return_value = np.array([[1.0] * 512, [1.0] * 512])
        result = manager.deduplicate_messages(msgs)
        assert len(result) == 1


class TestDeduplicateSessions:
    def test_empty_sessions(self, manager):
        assert manager.deduplicate_sessions([]) == []

    def test_sessions_deduplication(self, manager, mock_embedding):
        sessions = [
            {"question": "q1", "summary": "s1"},
            {"question": "q1", "summary": "s1"},
        ]
        mock_embedding.encode.return_value = np.array([[1.0] * 512, [1.0] * 512])
        result = manager.deduplicate_sessions(sessions)
        assert len(result) == 1


class TestCleanupOldMemories:
    def test_empty_memories(self, manager):
        assert manager.cleanup_old_memories([]) == []

    def test_keep_recent_memory(self, manager):
        from datetime import datetime
        memories = [{"timestamp": datetime.now(), "content": "recent"}]
        result = manager.cleanup_old_memories(memories, max_age_days=90)
        assert len(result) == 1

    def test_remove_old_memory(self, manager):
        from datetime import datetime, timedelta
        old = datetime.now() - timedelta(days=100)
        memories = [{"timestamp": old, "content": "old"}]
        result = manager.cleanup_old_memories(memories, max_age_days=90)
        assert len(result) == 0

    def test_keep_if_no_timestamp(self, manager):
        memories = [{"content": "no timestamp"}]
        result = manager.cleanup_old_memories(memories, max_age_days=90)
        assert len(result) == 1

    def test_string_timestamp_format(self, manager):
        from datetime import datetime, timedelta
        recent = (datetime.now() - timedelta(days=1)).isoformat()
        memories = [{"timestamp": recent, "content": "recent"}]
        result = manager.cleanup_old_memories(memories, max_age_days=90)
        assert len(result) == 1


class TestEstimateEntropy:
    def test_empty_messages(self, manager):
        result = manager.estimate_entropy([])
        assert result["total_messages"] == 0
        assert result["entropy_level"] == "low"
        assert result["recommendations"] == []

    def test_normal_messages(self, manager, mock_embedding):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        mock_embedding.encode.return_value = np.array([[1.0] * 512, [-1.0] * 512])
        result = manager.estimate_entropy(msgs)
        assert result["total_messages"] == 2
        assert "entropy_level" in result

    def test_many_messages_high_entropy(self, manager, mock_embedding):
        msgs = [{"role": "user", "content": f"msg-{i}"} for i in range(25)]
        mock_embedding.encode.return_value = np.random.random((25, 512)).astype(np.float32)
        result = manager.estimate_entropy(msgs)
        assert result["entropy_level"] == "high"
        assert len(result["recommendations"]) > 0

    def test_high_avg_length_triggers_high_entropy(self, manager, mock_embedding):
        long_text = "很长的消息" * 200  # > 1000 chars
        msgs = [{"role": "user", "content": long_text} for _ in range(5)]
        mock_embedding.encode.return_value = np.random.random((5, 512)).astype(np.float32)
        result = manager.estimate_entropy(msgs)
        assert result["avg_message_length"] > 500
        assert result["entropy_level"] == "high"


class TestCompressSessionHistory:
    def test_short_history_not_compressed(self, manager):
        msgs = [{"role": "user", "content": "hi"}] * 3
        # 不超过 max_messages 阈值
        result = manager._compress_by_truncation(msgs)
        # truncation 模式压缩后 <= 原始
        assert len(result) <= len(msgs)

    def test_truncation_compression(self, manager):
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = manager._compress_by_truncation(msgs)
        assert len(result) > 0
        # 每对 user+assistant 压缩为一条历史摘要
        assert len(result) == 2


class TestAutoClean:
    @pytest.mark.asyncio
    async def test_empty_messages(self, manager):
        result = await manager.auto_clean([])
        assert result == []

    @pytest.mark.asyncio
    async def test_low_entropy_no_clean(self, manager, mock_embedding):
        msgs = [{"role": "user", "content": "hi"}]
        mock_embedding.encode.return_value = np.array([[0.1] * 512])
        result = await manager.auto_clean(msgs)
        assert result == msgs
