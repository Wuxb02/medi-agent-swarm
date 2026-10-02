"""真实六卷冷备和全新命名空间恢复；需显式指定测试 Compose 项目。"""

import asyncio
import os
import subprocess
import uuid
from typing import TypedDict

import asyncmy
import pytest
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver
from langgraph.graph import END, START, StateGraph
from pymilvus import MilvusClient
from redis.asyncio import Redis

from mediZJ.infrastructure.settings import get_settings
from mediZJ.infrastructure.database import close_database
from mediZJ.memory.session_db import SessionDB
from scripts.backup import backup, restore

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]
PROJECT = os.environ.get("TEST_COMPOSE_PROJECT")


def command(*args, **kwargs):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, **kwargs
    )


class State(TypedDict):
    answer: str


def graph(saver):
    builder = StateGraph(State)
    builder.add_node("answer", lambda state: {"answer": state["answer"]})
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    return builder.compile(checkpointer=saver)


@pytest.mark.skipif(not PROJECT, reason="需 TEST_COMPOSE_PROJECT 指向独立测试部署")
async def test_all_volumes_backup_restore(mysql_infrastructure, tmp_path):
    assert PROJECT.startswith("medizj-validation"), "仅允许备份独立测试项目"
    token = uuid.uuid4().hex
    restored_project = f"medizj-restored-{token[:8]}"
    compose_files = ["compose.yaml", "compose.test.yaml"]
    env = {
        **os.environ,
        "MYSQL_PASSWORD": "test_password",
        "MYSQL_ROOT_PASSWORD": "test_root_password",
        "MINIO_ROOT_PASSWORD": "test_minio_password",
        "TEST_MYSQL_PORT": "24306",
        "TEST_REDIS_PORT": "27379",
        "TEST_MILVUS_PORT": "30530",
    }
    compose = [
        "compose",
        "-p",
        restored_project,
        "-f",
        *compose_files[:1],
        "-f",
        *compose_files[1:],
    ]
    db = SessionDB()
    owner = (await db.get_or_create_user("backup-user"))["user_id"]
    await db.save_turn(token, 0, {"content": "备份验证"}, {"content": token}, owner)
    config = {"configurable": {"thread_id": token}}
    async with AsyncMySaver.from_conn_string(get_settings().mysql_url) as saver:
        await saver.setup()
        await graph(saver).ainvoke({"answer": token}, config, durability="sync")
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    await redis.set(f"backup:{token}", token)
    await redis.aclose()
    collection = f"backup_{token}"
    milvus = MilvusClient(uri=get_settings().milvus_uri)
    await asyncio.to_thread(milvus.create_collection, collection, dimension=2)
    await asyncio.to_thread(
        milvus.upsert, collection, [{"id": 1, "vector": [0.1, 0.2], "proof": token}]
    )
    await asyncio.to_thread(milvus.flush, collection)
    await asyncio.to_thread(
        command,
        "run",
        "--rm",
        "--user",
        "0",
        "-v",
        f"{PROJECT}_uploads:/proof",
        "medizj-app:local",
        "python",
        "-c",
        f"from pathlib import Path; Path('/proof/{token}').write_text('{token}')",
    )
    await close_database()
    output = tmp_path / "backup"
    try:
        await asyncio.to_thread(
            backup, PROJECT, output, compose_files, "medizj-app:local"
        )
        await asyncio.to_thread(restore, restored_project, output, "medizj-app:local")
        with pytest.raises(ValueError, match="目标卷已存在"):
            await asyncio.to_thread(
                restore, restored_project, output, "medizj-app:local"
            )
        await asyncio.to_thread(
            command,
            *compose,
            "up",
            "-d",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "120",
            "mysql",
            "redis",
            "etcd",
            "minio",
            "milvus",
            env=env,
        )
        connection = await asyncmy.connect(
            host="127.0.0.1",
            port=24306,
            user="medizj",
            password="test_password",
            db="medizj_test",
        )
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT content FROM messages WHERE session_id=%s ORDER BY id", (token,)
            )
            assert await cursor.fetchall() == (("备份验证",), (token,))
        connection.close()
        async with AsyncMySaver.from_conn_string(
            "mysql+asyncmy://medizj:test_password@127.0.0.1:24306/medizj_test"
        ) as saver:
            assert (await graph(saver).aget_state(config)).values["answer"] == token
        restored_redis = Redis.from_url(
            "redis://127.0.0.1:27379", decode_responses=True
        )
        assert await restored_redis.get(f"backup:{token}") == token
        await restored_redis.aclose()
        restored_milvus = MilvusClient(uri="http://127.0.0.1:30530")
        rows = await asyncio.to_thread(
            restored_milvus.query,
            collection,
            filter="id == 1",
            output_fields=["proof"],
            consistency_level="Strong",
        )
        assert rows[0]["proof"] == token
        await asyncio.to_thread(restored_milvus.close)
        proof = await asyncio.to_thread(
            command,
            "run",
            "--rm",
            "-v",
            f"{restored_project}_uploads:/proof:ro",
            "medizj-app:local",
            "python",
            "-c",
            f"from pathlib import Path; print(Path('/proof/{token}').read_text())",
        )
        assert proof.stdout.strip() == token
    finally:
        await asyncio.to_thread(command, *compose, "down", "--volumes", env=env)
        await asyncio.to_thread(milvus.drop_collection, collection)
        await asyncio.to_thread(milvus.close)
