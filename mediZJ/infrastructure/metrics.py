"""跨实例持久化计数及基础设施运行指标。"""

from .database import transaction
from .redis_client import get_redis


async def increment(name: str, amount: int = 1) -> None:
    async with transaction() as conn:
        await conn.execute(
            "INSERT INTO metric_counters(name,value) VALUES (%s,%s) "
            "ON DUPLICATE KEY UPDATE value=value+%s",
            (name, amount, amount),
        )


async def snapshot() -> dict:
    async with transaction() as conn:
        runs = (
            await conn.execute(
                "SELECT status,COUNT(*) AS count FROM chat_runs GROUP BY status"
            )
        ).fetchall()
        jobs = (
            await conn.execute(
                "SELECT kind,status,COUNT(*) AS count FROM jobs GROUP BY kind,status"
            )
        ).fetchall()
        counters = (
            await conn.execute("SELECT name,value FROM metric_counters")
        ).fetchall()
        lag = (
            await conn.execute(
                "SELECT COUNT(*) AS pending,COALESCE(MAX(TIMESTAMPDIFF(SECOND,"
                "created_at,UTC_TIMESTAMP(6))),0) AS oldest_seconds FROM jobs "
                "WHERE kind IN ('knowledge_index','knowledge_delete','session_index',"
                "'session_delete') AND status IN ('pending','running','failed')"
            )
        ).fetchone()
        capacity = (
            await conn.execute(
                "SELECT resource,COUNT(*) AS active FROM capacity_slots "
                "WHERE lease_until>UTC_TIMESTAMP(6) GROUP BY resource"
            )
        ).fetchall()
    memory = await get_redis().info("memory")
    stats = await get_redis().info("stats")
    return {
        "runs": [dict(row) for row in runs],
        "jobs": [dict(row) for row in jobs],
        "counters": {row["name"]: row["value"] for row in counters},
        "index_lag": dict(lag),
        "capacity": [dict(row) for row in capacity],
        "redis_memory_bytes": memory["used_memory"],
        "redis_maxmemory_bytes": memory["maxmemory"],
        "redis_evicted_keys": stats["evicted_keys"],
    }
