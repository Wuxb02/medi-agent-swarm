"""后台处理器的业务幂等与外部写入后的补偿。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.api.models.chat import ChatRequest
from mediZJ.api.services.run_service import create_run
from mediZJ.infrastructure import handlers
from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.jobs import claim, enqueue
from mediZJ.memory.session_db import SessionDB

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


async def test_memory_uses_committed_result_and_missing_run_is_noop(
    mysql_infrastructure, monkeypatch
):
    owner = (await SessionDB().get_or_create_user("alice"))["user_id"]
    run = await create_run(ChatRequest(question="问题"), owner, None)
    async with transaction() as conn:
        await conn.execute(
            "UPDATE chat_runs SET result='{}' WHERE run_id=%s", (run["run_id"],)
        )
        await conn.execute(
            "UPDATE chat_runs SET result=JSON_OBJECT('answer','答案') WHERE run_id=%s",
            (run["run_id"],),
        )
    coordinator = MagicMock(_save_memory_candidates=AsyncMock())
    monkeypatch.setattr(
        "mediZJ.swarm.swarm_coordinator.SwarmCoordinator", lambda **kwargs: coordinator
    )
    await handlers.memory({"payload": {"run_id": run["run_id"]}})
    coordinator._save_memory_candidates.assert_awaited_once_with(
        run["session_id"],
        "问题",
        "答案",
        {"trace_id": run["run_id"]},
    )
    await handlers.memory({"payload": {"run_id": "missing"}})
    assert coordinator._save_memory_candidates.await_count == 1


@pytest.mark.parametrize("current", [None, {"turn_count": 2}])
async def test_session_index_compensates_delete_or_newer_turn(
    mysql_infrastructure, monkeypatch, current
):
    initial = {
        "session_id": "session",
        "user_id": "alice",
        "turn_count": 1,
        "first_question": "首问",
        "mode": "single",
        "created_at": "now",
        "total_tokens": 0,
    }
    get_session = AsyncMock(side_effect=[initial, current])
    monkeypatch.setattr(SessionDB, "get_session", get_session)
    vectors = MagicMock()
    monkeypatch.setattr(
        "mediZJ.memory.session_vector_store.SessionVectorStore", lambda: vectors
    )
    await enqueue(
        "session_index", "index", {"session_id": "session", "user_id": "alice"}
    )
    job = await claim("session_index", "worker")
    await handlers.session_index(job)
    vectors.index_session.assert_called_once()
    kind = "session_delete" if current is None else "session_index"
    repair = await claim(kind, "repair")
    assert repair["payload"]["session_id"] == "session"
    assert "repair" in repair["dedup_key"] or "after-index" in repair["dedup_key"]


async def test_lifecycle_failure_is_retried_not_silently_completed(
    mysql_infrastructure, monkeypatch
):
    service = MagicMock(prune_expired=AsyncMock(return_value={"status": "failed"}))
    monkeypatch.setattr("mediZJ.memory.lifecycle.DataLifecycleService", lambda: service)
    with pytest.raises(RuntimeError, match="生命周期清理失败"):
        await handlers.lifecycle({})
    service.prune_expired.return_value = {"status": "completed"}
    await handlers.lifecycle({})
