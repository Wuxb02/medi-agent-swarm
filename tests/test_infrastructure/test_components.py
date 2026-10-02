"""启动校验、任务監护与服务边界测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import ClientDisconnect
from pymilvus import DataType

from mediZJ.api.routers import chat
from mediZJ.infrastructure import bootstrap, context
from mediZJ.infrastructure.jobs import JobWorker, enqueue, claim
from mediZJ.infrastructure.settings import Settings
from mediZJ.infrastructure.vector_schema import SCHEMA_DESCRIPTION, validate_collection


@pytest.mark.parametrize(
    "values",
    [
        {"MYSQL_URL": "sqlite:///bad"},
        {"MILVUS_URI": "bad.db"},
        {"HEARTBEAT_SECONDS": "30"},
        {"BACKGROUND_LLM_LIMIT": "17"},
    ],
)
def test_invalid_deployment_configuration(values):
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            **{
                "mysql_url": "mysql+asyncmy://u:p@localhost/test",
                "redis_url": "redis://localhost",
                "milvus_uri": "http://localhost",
                **{key.lower(): value for key, value in values.items()},
            },
        )


def test_identity_has_no_model_supplied_fallback():
    with pytest.raises(RuntimeError):
        context.get_identity()
    token = context.execution_identity.set(("alice", "session"))
    try:
        assert context.get_identity() == ("alice", "session")
    finally:
        context.execution_identity.reset(token)


@pytest.mark.parametrize(
    "change", ["description", "dimension", "field", "auto_id", "vector"]
)
def test_vector_schema_rejects_incompatible_collection(change):
    description = {
        "description": SCHEMA_DESCRIPTION,
        "auto_id": False,
        "fields": [
            {"name": "id", "type": DataType.VARCHAR, "is_primary": True},
            {"name": "vector", "type": DataType.FLOAT_VECTOR, "params": {"dim": 512}},
        ],
    }
    dimension = 512
    if change == "description":
        description["description"] = "older-model"
    elif change == "dimension":
        dimension = 768
    elif change == "field":
        description["fields"][0]["type"] = DataType.INT64
    elif change == "vector":
        description["fields"][1]["params"]["dim"] = 768
    else:
        description["auto_id"] = True
    with pytest.raises(RuntimeError):
        validate_collection(
            description,
            {"id": DataType.VARCHAR, "vector": DataType.FLOAT_VECTOR},
            dimension,
        )


async def test_slow_response_releases_subscription_even_before_body(monkeypatch):
    settings = MagicMock(slow_client_timeout=0.01, event_subscriber_limit=1)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "_subscribers", 0)
    response = chat.subscribe("run", "alice")
    with pytest.raises(HTTPException):
        chat.subscribe("other", "alice")

    async def send(message):
        await asyncio.sleep(1)

    async def receive():
        await asyncio.sleep(1)
        return {"type": "http.disconnect"}

    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert chat._subscribers == 0


@pytest.mark.integration
@pytest.mark.infrastructure
async def test_worker_recovers_poll_failure_and_shutdown(
    mysql_infrastructure, monkeypatch
):
    done = asyncio.Event()

    async def handler(job):
        done.set()

    worker = JobWorker({"probe": handler}, concurrency=1)
    await enqueue("probe", "unique", {})
    original = claim
    calls = 0

    async def flaky(kind, owner):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("断线")
        return await original(kind, owner)

    monkeypatch.setattr("mediZJ.infrastructure.jobs.claim", flaky)
    await worker.start()
    await asyncio.wait_for(done.wait(), 3)
    await worker.stop()
    assert calls >= 2
    assert all(task.done() for task in worker.tasks)


@pytest.mark.integration
@pytest.mark.infrastructure
async def test_bootstrap_is_explicit_and_idempotent(mysql_infrastructure, monkeypatch):
    # 模型边界由索引测试覆盖；这里验证真实 MySQL/checkpointer 初始化。
    monkeypatch.setattr(bootstrap, "initialize_database", AsyncMock())
    monkeypatch.setattr(bootstrap, "close_database", AsyncMock())
    seed = AsyncMock()
    monkeypatch.setattr(
        "mediZJ.knowledge.default_documents.seed_default_documents", seed
    )
    knowledge = MagicMock()
    vectors = MagicMock()
    monkeypatch.setattr("mediZJ.knowledge.milvus_kb.MedicalKnowledgeBase", knowledge)
    monkeypatch.setattr(
        "mediZJ.memory.session_vector_store.SessionVectorStore", vectors
    )
    await bootstrap.setup()
    await bootstrap.setup()
    assert knowledge.call_count == 2
    assert vectors.call_count == 2
    assert seed.await_count == 2


@pytest.mark.integration
@pytest.mark.infrastructure
async def test_runtime_schema_only_validates_and_rejects_mismatch(mysql_infrastructure):
    from mediZJ.infrastructure.database import validate_runtime_schema, transaction
    from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver
    from mediZJ.infrastructure.settings import get_settings

    async with AsyncMySaver.from_conn_string(get_settings().mysql_url) as saver:
        await saver.setup()
    await validate_runtime_schema()
    async with transaction() as conn:
        await conn.execute("DELETE FROM capacity_slots WHERE slot_id='llm:0'")
    with pytest.raises(RuntimeError, match="容量配置不匹配"):
        await validate_runtime_schema()
    async with transaction() as conn:
        await conn.execute("DELETE FROM admission WHERE name='knowledge'")
    with pytest.raises(RuntimeError, match="未初始化"):
        await validate_runtime_schema()


async def test_evaluation_restores_profile_after_failure(monkeypatch):
    from mediZJ.eval.helpers import isolated_coordinator

    profile = MagicMock()
    profile.load = AsyncMock(return_value={"name": "原档案"})
    profile.load_records = AsyncMock(return_value=[{"id": "record"}])
    profile.load_pending = AsyncMock(return_value=[{"id": "pending"}])
    profile.save = AsyncMock()
    profile.save_records = AsyncMock()
    profile.save_pending = AsyncMock()
    monkeypatch.setattr("mediZJ.eval.helpers.PersonalProfile", lambda _: profile)
    coordinator = MagicMock()
    monkeypatch.setattr(
        "mediZJ.swarm.swarm_coordinator.SwarmCoordinator", lambda: coordinator
    )
    with pytest.raises(RuntimeError, match="评测中断"):
        async with isolated_coordinator() as current:
            assert current is coordinator
            assert profile.save.await_args.args == ({},)
            raise RuntimeError("评测中断")
    assert profile.save.await_args.args == ({"name": "原档案"},)
    assert profile.save_records.await_args.args == ([{"id": "record"}],)
    assert profile.save_pending.await_args.args == ([{"id": "pending"}],)


@pytest.mark.parametrize("failure", [False, True])
async def test_cli_initializes_and_closes_async_storage(monkeypatch, failure):
    from mediZJ import main as cli
    from mediZJ.infrastructure import database, redis_client

    monkeypatch.setattr(cli, "setup_logger", MagicMock())
    interactive = AsyncMock(side_effect=RuntimeError("中断") if failure else None)
    monkeypatch.setattr(cli, "interactive_mode", interactive)
    calls = {}
    for module, names in (
        (
            database,
            ["initialize_database", "validate_runtime_schema", "close_database"],
        ),
        (redis_client, ["initialize_redis", "close_redis"]),
    ):
        for name in names:
            calls[name] = AsyncMock()
            monkeypatch.setattr(module, name, calls[name])
    if failure:
        with pytest.raises(RuntimeError, match="中断"):
            await cli.main()
    else:
        await cli.main()
    interactive.assert_awaited_once()
    for call in calls.values():
        call.assert_awaited_once()


async def test_interactive_cli_keeps_session_and_handles_commands(monkeypatch, capsys):
    from mediZJ import main as cli

    monkeypatch.setattr(
        "builtins.input",
        MagicMock(
            side_effect=[
                "",
                "help",
                "clear",
                "一",
                "二",
                "三",
                "四",
                "失败",
                "quit",
            ]
        ),
    )
    process = AsyncMock(
        side_effect=[
            {"answer": "单轮", "suggestions": ["复诊"]},
            {"answer": "协作", "swarm_enabled": True, "agents_involved": ["agent"]},
            {"answer": "超时", "swarm_enabled": True, "timeout_occurred": True},
            {
                "answer": "部分超时",
                "swarm_enabled": True,
                "timeout_occurred": True,
                "agents_involved": ["agent"],
            },
            RuntimeError("模型失败"),
        ]
    )
    monkeypatch.setattr(cli, "process_with_swarm", process)
    await cli.interactive_mode()
    assert process.await_count == 5
    sessions = {call.kwargs["session_id"] for call in process.await_args_list}
    assert len(sessions) == 1
    output = capsys.readouterr().out
    assert "部分超时" in output and "模型失败" in output
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=KeyboardInterrupt))
    await cli.interactive_mode()
    assert "中断信号" in capsys.readouterr().out
