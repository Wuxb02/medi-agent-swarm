"""使用部署连接执行迁移。"""

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from mediZJ.infrastructure.schema import metadata
from mediZJ.infrastructure.settings import get_settings


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(get_settings().mysql_url)
    async with engine.connect() as connection:
        await connection.run_sync(migrate)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=get_settings().mysql_url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
