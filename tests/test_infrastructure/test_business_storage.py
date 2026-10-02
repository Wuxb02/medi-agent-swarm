"""真实 MySQL 业务查询、权限、原子轮次及上传元数据。"""

import asyncio

import pytest

from mediZJ.memory.session_db import SessionDB

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


async def test_concurrent_turns_and_owner_queries(mysql_infrastructure):
    db = SessionDB()
    alice = (await db.get_or_create_user("Alice"))["user_id"]
    bob = (await db.get_or_create_user("Bob"))["user_id"]
    assert (await db.get_user_by_id(alice))["username"] == "Alice"
    assert await db.get_user_by_id("missing") is None
    await asyncio.gather(
        *[
            db.save_turn(
                "same-session",
                0,
                {"content": str(index), "images": ["/uploads/test.png"]},
                {
                    "content": "回答",
                    "agent_events": [{"event": "done"}],
                    "suggestions": ["建议"],
                    "agents_involved": ["agent"],
                    "total_tokens": 10,
                },
                alice,
            )
            for index in range(2)
        ]
    )
    assert await db.get_turn_count("same-session") == 2
    assert await db.get_turn_count("missing") == 0
    detail = await db.get_session("same-session", alice)
    assert detail["turn_count"] == 2 and detail["total_tokens"] == 20
    assert [message["turn_index"] for message in detail["messages"]] == [0, 0, 1, 1]
    assert detail["messages"][0]["images"] == ["/uploads/test.png"]
    assert await db.get_session("same-session", bob) is None
    with pytest.raises(PermissionError):
        await db.save_turn(
            "same-session", 2, {"content": "越权"}, {"content": "越权"}, bob
        )
    assert (await db.get_session("same-session"))["turn_count"] == 2
    assert await db.count_sessions(alice) == 1
    assert await db.count_sessions(bob) == 0
    assert await db.count_sessions() == 1
    assert len(await db.list_sessions(1, 0, alice)) == 1
    assert await db.list_sessions(1, 1, alice) == []
    assert len(await db.list_sessions()) == 1
    assert await db.get_recent_turns("missing") == []
    assert not await db.delete_session("same-session", bob)
    assert await db.delete_session("same-session", alice)
    assert not await db.delete_session("missing")


async def test_uploads_and_profile_rows(mysql_infrastructure):
    db = SessionDB()
    owner = (await db.get_or_create_user("owner"))["user_id"]
    assert await db.get_profile(owner) is None
    await db.upsert_profile(owner, content="正文", pending="待确认")
    await db.upsert_profile(owner, pending="新待确认")
    row = await db.get_profile(owner)
    assert row["content"] == "正文" and row["pending"] == "新待确认"
    await db.save_upload("random.png", owner, "原图.png", "image/png", 8)
    assert (await db.get_upload("random.png"))["user_id"] == owner
    assert await db.get_upload("missing") is None
