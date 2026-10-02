"""共享异步 MySQL 连接池与事务。"""

import asyncio

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable

from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from .settings import get_settings

_engine: AsyncEngine | None = None
_current: ContextVar["Connection | None"] = ContextVar(
    "mysql_transaction", default=None
)


class Result:
    """缓冲查询结果，统一返回字段映射。"""

    def __init__(self, result: CursorResult) -> None:
        self.rowcount = result.rowcount
        self.lastrowid = result.lastrowid
        self._rows = result.mappings() if result.returns_rows else None

    def fetchone(self) -> Any:
        return self._rows.fetchone() if self._rows is not None else None

    def fetchall(self) -> list[Any]:
        return list(self._rows.fetchall()) if self._rows is not None else []


class Connection:
    """MySQL 原生参数化查询；禁止拼接用户输入。"""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection
        self.owner = asyncio.current_task()

    async def execute(self, query: str, parameters: tuple = ()) -> Result:
        return Result(await self.connection.exec_driver_sql(query, tuple(parameters)))

    async def executemany(self, query: str, parameters: list[tuple]) -> Result:
        return Result(await self.connection.exec_driver_sql(query, parameters))


async def initialize_database() -> None:
    global _engine
    settings = get_settings()
    _engine = create_async_engine(
        settings.mysql_url,
        pool_size=settings.mysql_pool_size,
        max_overflow=0,
        pool_timeout=10,
        pool_pre_ping=True,
        pool_recycle=1800,
        isolation_level="READ COMMITTED",
        connect_args={"init_command": "SET time_zone = '+00:00'"},
    )
    async with _engine.connect() as connection:
        version = (
            await connection.exec_driver_sql("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if version != "0004_knowledge_chunk_count":
            raise RuntimeError("数据库版本不匹配，请先执行 alembic upgrade head")


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("MySQL 连接池尚未初始化")
    return _engine


@asynccontextmanager
async def transaction() -> AsyncIterator[Connection]:
    """同一调用链共享事务，禁止跨任务共享连接。"""
    current = _current.get()
    if current is not None and current.owner is asyncio.current_task():
        yield current
        return
    async with get_engine().begin() as raw:
        connection = Connection(raw)
        token = _current.set(connection)
        try:
            from .jobs import assert_lease, job_context

            job = job_context.get()
            if job is not None:
                await assert_lease(connection, job)
            yield connection
        finally:
            _current.reset(token)


async def execute(callback: Callable, *args: Any, **kwargs: Any) -> Any:
    async with transaction() as connection:
        return await callback(connection, *args, **kwargs)


async def close_database() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def validate_runtime_schema() -> None:
    """应用只校验初始化状态，不创建表或调整容量。"""
    from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver

    settings = get_settings()
    async with transaction() as conn:
        names = {
            row["name"]
            for row in (await conn.execute("SELECT name FROM admission")).fetchall()
        }
        if not {"runs", "llm", "knowledge"}.issubset(names):
            raise RuntimeError("准入结构未初始化，请先运行 bootstrap")
        version = (
            await conn.execute("SELECT MAX(v) AS version FROM checkpoint_migrations")
        ).fetchone()["version"]
        if version != len(AsyncMySaver.MIGRATIONS) - 1:
            raise RuntimeError("图检查点 schema 版本不匹配，请运行 bootstrap")
        for resource, count in (
            ("llm", settings.llm_max_concurrency),
            ("llm_wait", settings.llm_queue_limit),
        ):
            rows = (
                await conn.execute(
                    "SELECT slot_id FROM capacity_slots WHERE resource=%s", (resource,)
                )
            ).fetchall()
            if {row["slot_id"] for row in rows} != {
                f"{resource}:{i}" for i in range(count)
            }:
                raise RuntimeError(
                    f"集群容量配置不匹配: {resource}，请先停止应用并运行 bootstrap"
                )
