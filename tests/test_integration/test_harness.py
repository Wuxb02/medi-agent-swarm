"""test_integration/test_harness.py — Harness Engineering 集成测试（需要真实 LLM）"""

import pytest
from mediZJ.swarm import SwarmCoordinator
from mediZJ.constraints import ConstraintValidator
from mediZJ.validation import AutoFixer
from mediZJ.memory import MemoryEntropyManager


@pytest.mark.integration
@pytest.mark.slow
class TestHarnessIntegration:
    @pytest.mark.asyncio
    async def test_constraint_validation_on_real_output(self):
        """在真实 LLM 输出上验证约束。"""
        coordinator = SwarmCoordinator()
        result = await coordinator.process(
            question="我今天有点头疼，需要注意什么？",
            session_id="test-harness-validate",
        )
        answer = result.get("response", result.get("answer", ""))
        validator = ConstraintValidator()
        validation = validator.validate_output("consultation_agent", answer)
        # 不强制要求 valid，只是验证不会崩溃
        assert "valid" in validation

    @pytest.mark.asyncio
    async def test_auto_fixer_on_real_output(self):
        """在真实 LLM 输出上测试自动修复。"""
        coordinator = SwarmCoordinator()
        result = await coordinator.process(
            question="我今天有点头疼，需要注意什么？",
            session_id="test-harness-fixer",
        )
        answer = result.get("response", result.get("answer", ""))
        fixer = AutoFixer()
        fixed = fixer.fix_high_risk_warning(answer)
        assert len(fixed) >= len(answer)

    @pytest.mark.asyncio
    async def test_entropy_management_on_real_history(self):
        """在真实对话历史上测试熵管理。"""
        coordinator = SwarmCoordinator()
        session_id = "test-harness-entropy"

        await coordinator.process(
            question="高血压患者应该注意什么？",
            session_id=session_id,
        )

        history = await coordinator.short_term_memory.get_history(session_id)
        assert len(history) > 0

        # 熵管理器需要 embedding 模型
        from mediZJ.memory.embedding import load_embedding_model
        embedding = load_embedding_model()
        manager = MemoryEntropyManager(embedding_client=embedding)
        entropy = manager.estimate_entropy(history)
        assert "entropy_level" in entropy
