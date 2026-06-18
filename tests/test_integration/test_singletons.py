"""test_integration/test_singletons.py — 单例验证集成测试（需要真实 LLM）"""

import pytest
from mediZJ.swarm import SwarmCoordinator
from mediZJ.memory import ShortTermMemory
from mediZJ.memory.session_db import SessionDB
from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase


@pytest.mark.integration
class TestSingletonIntegration:
    def test_short_term_memory_singleton(self):
        """ShortTermMemory 是单例。"""
        stm1 = ShortTermMemory()
        stm2 = ShortTermMemory()
        assert stm1 is stm2

    def test_medical_knowledge_base_singleton(self):
        """MedicalKnowledgeBase 是单例。"""
        kb1 = MedicalKnowledgeBase()
        kb2 = MedicalKnowledgeBase()
        assert kb1 is kb2

    def test_session_db_singleton(self):
        """SessionDB 是单例。"""
        db1 = SessionDB()
        db2 = SessionDB()
        assert db1 is db2

    @pytest.mark.asyncio
    async def test_no_duplicate_memory_save(self):
        """验证使用 Swarm 时短期记忆不会重复保存。"""
        coordinator = SwarmCoordinator()
        session_id = "test-no-dup"

        await coordinator.process(
            question="感冒了怎么办？只有流鼻涕。",
            session_id=session_id,
        )
        history = await coordinator.short_term_memory.get_history(session_id)
        assert len(history) >= 1
        # 验证没有重复：content 不应有连续重复
        contents = [h.get("content", "") for h in history]
        for i in range(len(contents) - 1):
            assert contents[i] != contents[i + 1] or contents[i] == ""
