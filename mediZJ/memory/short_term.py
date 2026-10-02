"""Redis 短期记忆：通过 MySQL 历史水位重建和 Lua 版本比较更新。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import asyncio
import random
import json
import uuid
from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.redis_client import get_redis
from mediZJ.infrastructure.settings import get_settings
from mediZJ.infrastructure.metrics import increment


@dataclass
class ConversationHistory:
    """对话历史数据类"""

    session_id: str
    messages: List[Dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    _uncompressed_start: int = 0  # 未压缩消息在 messages 中的起始索引

    def add_message(self, role: str, content: str):
        """添加消息"""
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.last_updated = datetime.now(timezone.utc)

    def get_recent_messages(self, limit: Optional[int] = 50) -> List[Dict[str, str]]:
        """获取最近的消息（limit 为 None 时返回全部）"""
        if limit is None:
            return list(self.messages)
        return self.messages[-limit:]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 Redis 存储）"""
        return {
            "session_id": self.session_id,
            "messages": self.messages,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "metadata": self.metadata,
            "_uncompressed_start": self._uncompressed_start,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationHistory":
        """从字典创建（从 Redis 加载）"""
        return cls(
            session_id=data["session_id"],
            messages=data["messages"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_updated=datetime.fromisoformat(data["last_updated"]),
            metadata=data.get("metadata", {}),
            _uncompressed_start=data.get("_uncompressed_start", len(data["messages"])),
        )


# 快照更新同时比较版本，防止跨实例读改写覆盖。
_COMPARE_AND_SET = """
local current = redis.call('HGET', KEYS[1], 'revision')
if (current or '0') ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'revision', ARGV[2], 'snapshot', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""


class ShortTermMemory:
    """按用户与会话隔离的 Redis 短期记忆。"""

    def __init__(self, user_id: str = "default") -> None:
        self.user_id = user_id
        self.settings = get_settings()

    def _key(self, session_id: str) -> str:
        return (
            f"medizj:{self.settings.app_environment}:memory:{self.user_id}:{session_id}"
        )

    async def _read(self, session_id: str):
        client = get_redis()
        key = self._key(session_id)
        async with client.pipeline(transaction=True) as pipe:
            pipe.hgetall(key)
            pipe.expire(key, self.settings.short_term_ttl)
            result, _ = await pipe.execute()
        if not result:
            await increment("cache_miss")
            return None, 0
        await increment("cache_hit")
        snapshot = json.loads(result["snapshot"])
        if snapshot["schema_version"] != 1:
            raise RuntimeError("短期记忆结构版本不匹配")
        return ConversationHistory.from_dict(snapshot["history"]), result["revision"]

    async def _write(self, history: ConversationHistory, revision: str | int) -> bool:
        snapshot = json.dumps(
            {"schema_version": 1, "history": history.to_dict()},
            ensure_ascii=False,
        )
        async with transaction():
            return bool(
                await get_redis().eval(
                    _COMPARE_AND_SET,
                    1,
                    self._key(history.session_id),
                    str(revision),
                    uuid.uuid4().hex,
                    snapshot,
                    str(self.settings.short_term_ttl),
                )
            )

    async def create_session(self, session_id: str, metadata=None):
        history = ConversationHistory(session_id, metadata=metadata or {})
        if not await self._write(history, 0):
            return await self.get_session(session_id)
        return history

    async def get_session(self, session_id: str):
        history, _ = await self._read(session_id)
        return history

    async def add_message(self, session_id: str, role: str, content: str):
        for attempt in range(8):
            history, revision = await self._read(session_id)
            history = history or ConversationHistory(session_id)
            history.add_message(role, content)
            if await self._write(history, revision):
                return
            await asyncio.sleep(random.uniform(0.001, 0.005) * 2**attempt)
        raise RuntimeError("短期记忆并发更新冲突")

    async def restore_session(self, session_id: str, messages: list[dict]) -> bool:
        watermark = max((int(m.get("id", 0)) for m in messages), default=0)
        for attempt in range(8):
            history, revision = await self._read(session_id)
            if history and history.metadata.get("watermark", -1) >= watermark:
                return False
            history = ConversationHistory(
                session_id,
                messages=list(messages),
                metadata={"watermark": watermark},
            )
            if await self._write(history, revision):
                await increment("cache_rebuild")
                return True
            await asyncio.sleep(random.uniform(0.001, 0.005) * 2**attempt)
        raise RuntimeError("短期记忆重建并发冲突")

    async def get_recent_messages(self, session_id: str, limit=50):
        history = await self.get_session(session_id)
        return history.get_recent_messages(limit) if history else []

    async def get_history(self, session_id: str, limit: int = 10):
        messages = await self.get_recent_messages(session_id, limit * 2)
        return [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] in {"user", "assistant"}
        ]

    async def get_all_messages(self, session_id: str):
        return await self.get_recent_messages(session_id, None)

    async def clear_session(self, session_id: str) -> None:
        await get_redis().delete(self._key(session_id))

    async def merge_sub_session(
        self,
        main_session_id: str,
        sub_session_id: str,
        summary_text: str,
        role: str = "assistant",
    ) -> None:
        await self.add_message(main_session_id, role, summary_text)
        await self.clear_session(sub_session_id)
