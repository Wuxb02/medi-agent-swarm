"""部署前单次初始化；不导入或修改旧业务数据。"""

import asyncio

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver

from .database import close_database, initialize_database, transaction
from .settings import get_settings


async def setup():
    await initialize_database()
    settings = get_settings()
    try:
        async with transaction() as conn:
            for name in ("runs", "llm", "knowledge"):
                await conn.execute("INSERT IGNORE INTO admission VALUES (%s)", (name,))
            await conn.execute(
                "INSERT INTO users (user_id,username,username_normalized,role,is_active,created_at) "
                "VALUES ('default','default','default','user',1,DATE_FORMAT(UTC_TIMESTAMP(),"
                "'%%Y-%%m-%%dT%%H:%%i:%%s+00:00')) ON DUPLICATE KEY UPDATE user_id=user_id"
            )
            for resource, count in (
                ("llm", settings.llm_max_concurrency),
                ("llm_wait", settings.llm_queue_limit),
            ):
                await conn.execute(
                    "SELECT name FROM admission WHERE name='llm' FOR UPDATE"
                )
                extra = (
                    await conn.execute(
                        "SELECT slot_id FROM capacity_slots WHERE resource=%s AND "
                        "CAST(SUBSTRING_INDEX(slot_id,':',-1) AS UNSIGNED)>=%s AND "
                        "lease_until>UTC_TIMESTAMP(6) FOR UPDATE",
                        (resource, count),
                    )
                ).fetchone()
                if extra:
                    raise RuntimeError("缩减容量前必须停止应用并等待旧租约结束")
                await conn.execute(
                    "DELETE FROM capacity_slots WHERE resource=%s AND "
                    "CAST(SUBSTRING_INDEX(slot_id,':',-1) AS UNSIGNED)>=%s",
                    (resource, count),
                )
                for i in range(count):
                    await conn.execute(
                        "INSERT IGNORE INTO capacity_slots(slot_id,resource) VALUES (%s,%s)",
                        (f"{resource}:{i}", resource),
                    )
        async with AsyncMySaver.from_conn_string(settings.mysql_url) as saver:
            await saver.setup()
        from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
        from mediZJ.memory.session_vector_store import SessionVectorStore

        await asyncio.to_thread(MedicalKnowledgeBase, initialize=True)
        await asyncio.to_thread(SessionVectorStore, initialize=True)
    finally:
        await close_database()


if __name__ == "__main__":
    command.upgrade(Config("alembic.ini"), "head")
    asyncio.run(setup())
