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
from mediZJ.memory.scripts.migrate_structured_memory import migrate


@pytest.fixture
def store(tmp_path):
    SessionDB.reset()
    db = SessionDB(str(tmp_path / "memory.db"))
    yield StructuredMemoryStore(db)
    SessionDB.reset()


def test_active_revision_and_pending_isolation(store):
    store.upsert_active("u1", "profile_fact", "年龄", "30岁")
    first_revision = store.get_profile_revision("u1")
    store.add_pending("u1", "profile_fact", "吸烟史", "10年")
    assert store.get_profile_revision("u1") == first_revision
    assert [item["memory_key"] for item in store.list_items("u1")] == ["年龄"]

    store.upsert_active("u1", "profile_fact", "年龄", "31岁")
    active = store.list_items("u1")
    assert active[0]["value"] == "31岁"
    assert store.get_profile_revision("u1") == first_revision + 1
    assert len(store.list_items("u1", statuses=("superseded",))) == 1


def test_authority_pending_episode_and_usage_lifecycle(store):
    clinician_id = store.upsert_active(
        "u1",
        "profile_fact",
        "过敏史",
        "青霉素",
        source_type="clinician_confirmed",
    )
    assert store.upsert_active(
        "u1", "profile_fact", "过敏史", "无", source_type="user_reported"
    ) == clinician_id
    store.replace_active("u1", "profile_fact", {"年龄": "30"})
    assert store.deactivate("u1", "profile_fact", "不存在") is False

    pending_id = store.add_pending(
        "u1", "profile_fact", "吸烟史", "10年", confidence=0.9
    )
    assert store.add_pending(
        "u1", "profile_fact", "吸烟史", "10年", confidence=0.9
    ) == pending_id
    assert store.confirm_pending("u1", "吸烟史", "错误") is False
    assert store.confirm_pending("u1", "吸烟史", "10年") is True
    store.add_pending("u1", "medical_record", "2026:感冒", {"description": "感冒"})
    assert store.dismiss_pending("u1", "2026:感冒", "感冒") is True

    episode_id = store.save_episodic_summary("s0", "u1", "旧会话", {"症状": "头痛"})
    assert store.save_episodic_summary("s0", "u1", "更新摘要") == episode_id
    episodes = store.recall_episodes("u1", "s1")
    assert episodes[0]["summary"] == "更新摘要"
    store.record_usage(
        [clinician_id],
        session_id="s1",
        trace_id="t1",
        agent_id="lead",
        user_id="u1",
    )
    store.set_profile_hash("u1", "hash")
    with store.db._get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_usage").fetchone()[0] == 1
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        conn.execute(
            "UPDATE user_memory_items SET effective_at = ? WHERE memory_id = ?",
            (future, clinician_id),
        )
        conn.commit()
    assert all(item["memory_id"] != clinician_id for item in store.list_items("u1"))


def test_deterministic_serialization_and_tool_order():
    assert canonical_json({"b": {2, 1}, "a": {"d": 2, "c": 1}}) == (
        '{"a":{"c":1,"d":2},"b":[1,2]}'
    )
    tools = [
        {"type": "function", "function": {"name": "z", "parameters": {}}},
        {"function": {"parameters": {}, "name": "a"}, "type": "function"},
    ]
    assert [item["function"]["name"] for item in canonical_tools(tools)] == [
        "a",
        "z",
    ]


@pytest.mark.asyncio
async def test_context_prefix_is_stable_and_dynamic_tail_does_not_change_hash(store):
    store.upsert_active("u1", "profile_fact", "性别", "女")
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
    store.upsert_active("u1", "profile_fact", "年龄", "30")
    store.upsert_active("u2", "profile_fact", "年龄", "40")
    working = type(
        "Working",
        (),
        {"get_recent_messages": AsyncMock(return_value=[])},
    )()
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
        for index, user_id in enumerate(("u1", "u2"), 1)
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


def test_legacy_migration_supports_dry_run_and_is_idempotent(tmp_path):
    SessionDB.reset()
    db_path = tmp_path / "migration.db"
    db = SessionDB(str(db_path))
    db.upsert_profile(
        "u1",
        content="# 患者档案\n\n## 个人信息\n- 年龄：30岁\n",
        pending=(
            "# 待确认信息\n\n"
            "- [信息]吸烟史：10年（2025-05-16 提取，置信度：高）\n"
        ),
    )
    dry_report = migrate(str(db_path), dry_run=True)
    assert dry_report["active_profile_facts"] == 1
    with db._get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_memory_items").fetchone()[0] == 0

    report = migrate(str(db_path))
    assert report["pending_items"] == 1
    assert migrate(str(db_path)) == {
        "users": 0,
        "active_profile_facts": 0,
        "active_medical_records": 0,
        "pending_items": 0,
    }
