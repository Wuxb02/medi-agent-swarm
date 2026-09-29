"""双轨提取的门控、隔离和去重测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.memory.dual_extraction import DualMemoryExtractor


@pytest.mark.asyncio
async def test_both_lanes_run_independently():
    profile = MagicMock()
    profile.load.return_value = {}
    profile.load_pending.return_value = []
    profile.load_records.return_value = []
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=[
        '{"stable_info":[{"key":"慢性病史","value":"高血压",'
        '"confidence":"high","source_text":"我有高血压"}],'
        '"medical_records":[]}',
        '{"claims":[]}',
    ])
    jev = MagicMock()
    jev.choice = AsyncMock(return_value=("yes", 0.99))
    candidates = MagicMock()
    candidates.catalog._connection.return_value.__enter__.return_value = MagicMock()
    extractor = DualMemoryExtractor(llm, profile, jev, candidates)

    await extractor.process("turn-1", "user-1", "我有高血压", "请就医")

    profile.add_pending.assert_called_once()
    assert profile.add_pending.call_args.args[0][0]["value"] == "高血压"
    assert jev.choice.await_count == 2


@pytest.mark.asyncio
async def test_one_gate_failure_does_not_stop_other_lane():
    profile = MagicMock()
    llm = MagicMock()
    llm.chat = AsyncMock(return_value='{"claims":[]}')
    jev = MagicMock()

    async def gate(_state, name, *_args):
        if name == "personal":
            raise TimeoutError("JEV timeout")
        return "yes", 0.95

    jev.choice = AsyncMock(side_effect=gate)
    candidates = MagicMock()
    connection = candidates.catalog._connection.return_value.__enter__.return_value
    connection.execute.return_value.fetchone.return_value = None
    extractor = DualMemoryExtractor(llm, profile, jev, candidates)

    await extractor.process("turn-2", "user-1", "高血压是什么", "高血压需随访")

    llm.chat.assert_awaited_once()
    profile.add_pending.assert_not_called()


@pytest.mark.asyncio
async def test_general_question_does_not_create_personal_fact():
    profile = MagicMock()
    llm = MagicMock()
    llm.chat = AsyncMock()
    jev = MagicMock()
    jev.choice = AsyncMock(return_value=("no", 0.99))
    candidates = MagicMock()
    connection = candidates.catalog._connection.return_value.__enter__.return_value
    connection.execute.return_value.fetchone.return_value = None
    extractor = DualMemoryExtractor(llm, profile, jev, candidates)

    await extractor.process("turn-3", "user-1", "高血压患者怎么办", "请就医")

    llm.chat.assert_not_awaited()
    profile.add_pending.assert_not_called()


@pytest.mark.asyncio
async def test_personal_rejects_unquoted_and_existing_facts():
    profile = MagicMock()
    profile.load.return_value = {"年龄": "30岁"}
    profile.load_pending.return_value = []
    profile.load_records.return_value = []
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=(
        '{"stable_info":['
        '{"key":"年龄","value":"30岁","source_text":"我30岁"},'
        '{"key":"过敏史","value":"青霉素","source_text":"不存在的原文"}'
        '],"medical_records":[]}'
    ))
    jev = MagicMock()
    jev.choice = AsyncMock(return_value=("yes", 0.9))
    extractor = DualMemoryExtractor(llm, profile, jev, MagicMock())

    await extractor._personal("我30岁")

    profile.add_pending.assert_called_once_with([])


@pytest.mark.asyncio
async def test_knowledge_saves_only_quoted_claim_with_supported_evidence():
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=[
        '{"claims":[{"claim":"高血压需要随访",'
        '"source_text":"高血压需要随访"},'
        '{"claim":"虚构事实","source_text":"不存在的原文"}]}',
        '{"verdict":"support","quote":"高血压应定期随访"}',
    ])
    jev = MagicMock()
    jev.choice = AsyncMock(return_value=("yes", 0.9))
    candidates = MagicMock()
    candidates.evidence_hits.return_value = [{
        "document_id": "doc", "version_id": "v1",
        "source_url": "https://example.org",
        "excerpt": "高血压应定期随访。",
    }]
    extractor = DualMemoryExtractor(llm, MagicMock(), jev, candidates)

    await extractor._knowledge(
        "turn-1", "user-1", "高血压是什么", "高血压需要随访"
    )

    evidence = candidates.add_candidate.call_args.args[4]
    assert evidence[0]["verdict"] == "support"
    assert evidence[0]["quote"] == "高血压应定期随访"
    candidates.add_candidate.assert_called_once()


@pytest.mark.asyncio
async def test_evidence_rejects_nonmatching_quote():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=(
        '{"verdict":"support","quote":"不存在于片段的文本"}'
    ))
    extractor = DualMemoryExtractor(llm, MagicMock(), MagicMock(), MagicMock())
    hits = [{"excerpt": "另一段资料", "version_id": "v1"}]

    assert await extractor._judge_evidence("主张", hits) == []
