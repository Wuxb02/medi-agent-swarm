"""结构化记忆与 KV cache 稳定前缀测试。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest

from mediZJ.memory.context_builder import MedicalMemoryContextBuilder
from mediZJ.memory.prompt_prefix import (
    PromptPrefixAssembler,
    canonical_json,
    canonical_tools,
)
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.structured_memory import StructuredMemoryStore

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


@pytest.fixture
def store(mysql_infrastructure):
    SessionDB.reset()
    db = SessionDB()
    yield StructuredMemoryStore(db)
    SessionDB.reset()


async def test_active_revision_and_pending_isolation(store):
    await store.upsert_active("u1", "profile_fact", "年龄", "30岁")
    first_revision = await store.get_profile_revision("u1")
    await store.add_pending("u1", "profile_fact", "吸烟史", "10年")
    assert await store.get_profile_revision("u1") == first_revision
    assert [item["memory_key"] for item in await store.list_items("u1")] == ["年龄"]
    await store.upsert_active("u1", "profile_fact", "年龄", "31岁")
    active = await store.list_items("u1")
    assert active[0]["value"] == "31岁"
    assert await store.get_profile_revision("u1") == first_revision + 1
    assert len(await store.list_items("u1", statuses=("superseded",))) == 1


@pytest.mark.parametrize("memory_ids", [[], iter(())])
async def test_empty_usage_does_not_write_sql(store, execute_sql, memory_ids):
    """无用户记忆时，真实数据库不执行空批次 INSERT。"""
    await store.record_usage(
        memory_ids, session_id="s1", trace_id="t1", agent_id="lead", user_id="u1"
    )
    row = (await execute_sql("SELECT COUNT(*) AS count FROM memory_usage")).fetchone()
    assert row["count"] == 0


async def test_authority_pending_episode_and_usage_lifecycle(store, execute_sql):
    clinician_id = await store.upsert_active(
        "u1", "profile_fact", "过敏史", "青霉素", source_type="clinician_confirmed"
    )
    assert (
        await store.upsert_active(
            "u1", "profile_fact", "过敏史", "无", source_type="user_reported"
        )
        == clinician_id
    )
    await store.replace_active("u1", "profile_fact", {"年龄": "30"})
    assert await store.deactivate("u1", "profile_fact", "不存在") is False
    pending_id = await store.add_pending(
        "u1", "profile_fact", "吸烟史", "10年", confidence=0.9
    )
    assert (
        await store.add_pending("u1", "profile_fact", "吸烟史", "10年", confidence=0.9)
        == pending_id
    )
    assert await store.confirm_pending("u1", "吸烟史", "错误") is False
    assert await store.confirm_pending("u1", "吸烟史", "10年") is True
    await store.add_pending(
        "u1", "medical_record", "2026:感冒", {"description": "感冒"}
    )
    assert await store.dismiss_pending("u1", "2026:感冒", "感冒") is True
    episode_id = await store.save_episodic_summary(
        "s0", "u1", "旧会话", {"症状": "头痛"}
    )
    assert await store.save_episodic_summary("s0", "u1", "更新摘要") == episode_id
    episodes = await store.recall_episodes("u1", "s1")
    assert episodes[0]["summary"] == "更新摘要"
    await store.record_usage(
        [clinician_id], session_id="s1", trace_id="t1", agent_id="lead", user_id="u1"
    )
    await store.set_profile_hash("u1", "hash")
    assert (await execute_sql("SELECT COUNT(*) AS count FROM memory_usage")).fetchone()[
        "count"
    ] == 1
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    await execute_sql(
        "UPDATE user_memory_items SET effective_at = %s WHERE memory_id = %s",
        (future, clinician_id),
    )
    assert all(
        (item["memory_id"] != clinician_id for item in await store.list_items("u1"))
    )


def test_deterministic_serialization_and_tool_order():
    assert (
        canonical_json({"b": {2, 1}, "a": {"d": 2, "c": 1}})
        == '{"a":{"c":1,"d":2},"b":[1,2]}'
    )
    tools = [
        {"type": "function", "function": {"name": "z", "parameters": {}}},
        {"function": {"parameters": {}, "name": "a"}, "type": "function"},
    ]
    assert [item["function"]["name"] for item in canonical_tools(tools)] == ["a", "z"]


@pytest.mark.asyncio
async def test_context_prefix_is_stable_and_dynamic_tail_does_not_change_hash(store):
    await store.upsert_active("u1", "profile_fact", "性别", "女")
    working = type(
        "Working",
        (),
        {
            "get_recent_messages": AsyncMock(
                return_value=[{"role": "user", "content": "历史问题"}]
            )
        },
    )()
    builder = MedicalMemoryContextBuilder(store=store, working_memory=working)
    first = await builder.build(
        session_id="s1",
        user_id="u1",
        query="头痛",
        agent_id="lead_agent",
        call_type="lead_assessment",
        base_system_prompt="稳定系统提示",
        evidence_chunks=[{"content": "证据 A", "score": 0.9}],
    )
    second = await builder.build(
        session_id="s1",
        user_id="u1",
        query="腹痛",
        agent_id="lead_agent",
        call_type="lead_assessment",
        base_system_prompt="稳定系统提示",
        evidence_chunks=[{"content": "证据 B", "score": 0.1}],
    )
    assert first.global_prefix_hash == second.global_prefix_hash
    assert first.profile_prefix_hash == second.profile_prefix_hash
    assert first.prompt_messages()[:2] == second.prompt_messages()[:2]
    assert "score" not in first.user_stable_prefix
    assert "## 当前任务\n头痛" in first.prompt_messages()[-1]["content"]


@pytest.mark.asyncio
async def test_global_prefix_is_shared_across_users(store):
    await store.upsert_active("u1", "profile_fact", "年龄", "30")
    await store.upsert_active("u2", "profile_fact", "年龄", "40")
    working = type("Working", (), {"get_recent_messages": AsyncMock(return_value=[])})()
    builder = MedicalMemoryContextBuilder(store=store, working_memory=working)
    contexts = [
        await builder.build(
            session_id=f"s{index}",
            user_id=user_id,
            query="q",
            agent_id="consultation_agent",
            call_type="consultation_agent",
            base_system_prompt="system",
        )
        for (index, user_id) in enumerate(("u1", "u2"), 1)
    ]
    assert contexts[0].global_prefix_hash == contexts[1].global_prefix_hash
    assert contexts[0].profile_prefix_hash != contexts[1].profile_prefix_hash


def test_user_prefix_has_fixed_field_order():
    memories = [
        {
            "memory_id": "2",
            "memory_type": "profile_fact",
            "memory_key": "性别",
            "value": "女",
        },
        {
            "memory_id": "1",
            "memory_type": "profile_fact",
            "memory_key": "年龄",
            "value": "30",
        },
    ]
    prefix = PromptPrefixAssembler.user_prefix(memories)
    assert prefix.index("年龄") < prefix.index("性别")
