"""应用生命周期管理的异步 Redis 连接池。"""

from redis.asyncio import Redis

from .settings import get_settings

_client: Redis | None = None


async def initialize_redis() -> None:
    global _client
    settings = get_settings()
    _client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_timeout,
        socket_timeout=settings.redis_timeout,
        max_connections=64,
    )
    await _client.ping()


def get_redis() -> Redis:
    if _client is None:
        raise RuntimeError("Redis 连接池尚未初始化")
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
