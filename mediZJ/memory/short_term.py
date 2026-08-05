"""
短期记忆：会话级对话历史管理

功能：
- 管理会话级的对话历史（messages）
- 支持两种存储后端：内存（默认）和 Redis（可选）
- 自动过期机制（默认 1 小时，可配置）
- 熵管理：自动去重和压缩（Harness Engineering）
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import asyncio
import json
import time
from loguru import logger

# Harness Engineering: 熵管理
try:
    from .entropy_manager import MemoryEntropyManager
    from .embedding import load_embedding_model
    ENTROPY_ENABLED = True
except ImportError:
    logger.warning("EntropyManager not found, running without entropy management")
    ENTROPY_ENABLED = False


@dataclass
class ConversationHistory:
    """对话历史数据类"""
    session_id: str
    messages: List[Dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    _uncompressed_start: int = 0  # 未压缩消息在 messages 中的起始索引

    def add_message(self, role: str, content: str):
        """添加消息"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.last_updated = datetime.now()

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
            "_uncompressed_start": self._uncompressed_start
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
            _uncompressed_start=data.get("_uncompressed_start", len(data["messages"]))
        )


class ShortTermMemory:
    """
    短期记忆管理器（单例模式）

    支持两种存储后端：
    1. memory：纯内存存储（默认，快速但不持久）
    2. redis：Redis 存储（可选，持久但需要 Redis 服务）

    使用场景：
    - 管理单次会话的对话历史
    - Agent Loop 中的消息记录
    - 会话结束后转换为长期记忆
    """

    _instance = None  # 单例实例

    def __new__(cls, *args, **kwargs):
        """单例模式：确保只有一个 ShortTermMemory 实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        storage_type: str = "memory",
        redis_config: Optional[Dict[str, Any]] = None,
        llm_client=None,
        ttl_seconds: int = 3600
    ):
        """
        初始化短期记忆管理器

        Args:
            storage_type: 存储类型，"memory" 或 "redis"
            redis_config: Redis 配置（storage_type="redis" 时需要）
            llm_client: LLM 客户端（可选），用于熵管理器的语义摘要生成
            ttl_seconds: 会话过期时间（秒），默认 3600（1 小时）。memory 和 redis 模式均生效
        """
        # 防止重复初始化
        if hasattr(self, '_initialized'):
            return

        self.storage_type = storage_type
        self.ttl_seconds = ttl_seconds
        self.sessions: Dict[str, ConversationHistory] = {}
        # per-session 写锁：防止同会话并发 add_message 与增量压缩交错导致消息丢失
        self._session_locks: Dict[str, asyncio.Lock] = {}
        # 全量过期清理的节流时间戳（monotonic），避免每次写入都 O(n) 全扫
        self._last_evict_at: float = 0.0
        self.redis_client = None
        self._initialized = True

        # Harness Engineering: 熵管理器
        if ENTROPY_ENABLED:
            embedding_client = load_embedding_model()
            self.entropy_manager = MemoryEntropyManager(embedding_client=embedding_client, llm_client=llm_client)
            logger.debug("✅ Entropy management enabled for short-term memory")
        else:
            self.entropy_manager = None

        if storage_type == "redis":
            try:
                import redis
                config = redis_config or {}
                self.redis_client = redis.Redis(
                    host=config.get("host", "localhost"),
                    port=config.get("port", 6379),
                    db=config.get("db", 0),
                    password=config.get("password"),
                    decode_responses=True
                )
                # 测试连接
                self.redis_client.ping()
                logger.info("ShortTermMemory initialized with Redis")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}. Falling back to memory storage.")
                self.storage_type = "memory"
                self.redis_client = None
        else:
            logger.info("ShortTermMemory initialized with in-memory storage")

    def create_session(
        self,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ConversationHistory:
        """
        创建新会话

        Args:
            session_id: 会话ID
            metadata: 会话元数据

        Returns:
            ConversationHistory 对象
        """
        history = ConversationHistory(
            session_id=session_id,
            metadata=metadata or {}
        )

        if self.storage_type == "memory":
            self.sessions[session_id] = history
        elif self.storage_type == "redis" and self.redis_client:
            self._save_to_redis(history)

        logger.debug(f"Created session: {session_id}")
        return history

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """获取（或创建）指定会话的写锁"""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str
    ):
        """
        添加消息到会话历史（写入时自动增量压缩）

        Args:
            session_id: 会话ID
            role: 消息角色（user/assistant/tool）
            content: 消息内容
        """
        # 周期性触发全量过期清理（60s 节流，memory 模式生效）
        now = time.monotonic()
        if now - self._last_evict_at > 60:
            self._evict_expired_sessions()
            self._last_evict_at = now

        async with self._get_session_lock(session_id):
            history = self.get_session(session_id)

            if history is None:
                history = self.create_session(session_id)

            history.add_message(role, content)

            # 写入时增量压缩：仅当未压缩消息满足高熵条件时触发
            if self.entropy_manager:
                await self._maybe_compress_incremental(history)

            # 保存到存储
            if self.storage_type == "redis" and self.redis_client:
                self._save_to_redis(history)

        logger.debug(f"Added {role} message to session {session_id}")

    async def _maybe_compress_incremental(self, history: ConversationHistory):
        """
        增量压缩：只对未压缩部分检查熵，满足高熵条件时压缩较旧的消息。

        策略：
        - 保留最近 keep_recent 条消息不动
        - 对更早的未压缩消息执行熵检查（high 时才压缩）
        - 压缩摘要以 role="assistant" 存入（兼容 get_history 过滤）
        - 更新 _uncompressed_start 指针，实现累积增长
        """
        messages = history.messages
        start = history._uncompressed_start
        keep_recent = 5  # 保留最近 5 条不动

        # 可压缩区间: [start, len(messages) - keep_recent)
        compressible_end = len(messages) - keep_recent
        if compressible_end <= start:
            return  # 可压缩部分不足

        compressible = messages[start:compressible_end]

        # 熵检查：仅当高熵时才触发压缩（encode 为 CPU 密集，下线程避免阻塞事件循环）
        entropy = await asyncio.to_thread(
            self.entropy_manager.estimate_entropy, compressible
        )
        if entropy["entropy_level"] != "high":
            return  # 不满足压缩条件，跳过

        logger.info(
            f"📊 会话 {history.session_id} 未压缩部分熵等级: high "
            f"(消息数: {entropy['total_messages']}, "
            f"重复率: {entropy['duplicate_rate']:.1%})"
        )

        # 执行压缩
        # 1. 去重（重复率 > 0.1 才执行）
        cleaned = compressible
        if entropy.get("duplicate_rate", 0) > 0.1:
            cleaned = await asyncio.to_thread(
                self.entropy_manager.deduplicate_messages, cleaned
            )

        # 2. LLM 摘要（或截断降级）
        compressed = await self.entropy_manager._compress_older_messages(
            cleaned, entropy=entropy
        )

        # 3. 修正 role: system → assistant（兼容 get_history 过滤）
        for msg in compressed:
            if msg.get("role") == "system":
                msg["role"] = "assistant"

        # 4. 替换原区间，更新压缩边界
        history.messages = messages[:start] + compressed + messages[compressible_end:]
        history._uncompressed_start = start + len(compressed)

        logger.info(
            f"📦 增量压缩完成: {len(compressible)} 条 → {len(compressed)} 条摘要 "
            f"(session={history.session_id}, 未压缩边界={history._uncompressed_start})"
        )

    def _is_expired(self, session: ConversationHistory) -> bool:
        """检查会话是否过期（基于 last_updated）"""
        return datetime.now() - session.last_updated > timedelta(seconds=self.ttl_seconds)

    def _evict_expired_sessions(self):
        """惰性清理所有过期会话（memory 模式）"""
        if self.storage_type != "memory":
            return
        expired_ids = [
            sid for sid, s in self.sessions.items()
            if self._is_expired(s)
        ]
        for sid in expired_ids:
            logger.debug(f"会话 {sid} 已过期，自动清除（TTL={self.ttl_seconds}s）")
            self.sessions.pop(sid, None)
            self._session_locks.pop(sid, None)

    def get_session(self, session_id: str) -> Optional[ConversationHistory]:
        """
        获取会话历史（惰性过期检查）

        Args:
            session_id: 会话ID

        Returns:
            ConversationHistory 对象，如果不存在或已过期返回 None
        """
        if self.storage_type == "memory":
            session = self.sessions.get(session_id)
            if session is None:
                return None
            if self._is_expired(session):
                logger.info(f"会话 {session_id} 已过期，惰性清除（TTL={self.ttl_seconds}s）")
                self.sessions.pop(session_id, None)
                self._session_locks.pop(session_id, None)
                return None
            return session
        elif self.storage_type == "redis" and self.redis_client:
            return self._load_from_redis(session_id)
        return None

    async def restore_session(
        self,
        session_id: str,
        messages: List[Dict[str, str]],
    ) -> bool:
        """从 SQLite 回填会话历史到短期记忆（批量、幂等）

        用于"最近会话恢复"：服务重启或 TTL 过期后短期记忆清空，续聊时
        从权威源 SQLite 重建完整消息历史，供 _retrieve_memories 消费。

        实现要点：
        - 空 messages / Redis 后端（本就持久）直接跳过。
        - 单次 per-session 锁覆盖整批，与 add_message 互斥防交错丢消息。
        - 已有消息则视为已恢复，返回 False 不覆盖（同会话连续提问幂等）。
        - 回填后按现有压缩策略处理：熵为 high 时压缩较旧消息（含 LLM 摘要），
          低熵保持完整，全量上下文始终可用。
        - 保留 SQLite 原始 timestamp，刷新 last_updated 使 TTL 重新计时。

        Args:
            session_id: 会话 ID
            messages: 消息列表（按时间正序），每条含 role/content/timestamp

        Returns:
            True 表示本次执行了回填；False 表示跳过（空/Redis/已有消息）
        """
        if not messages:
            return False
        if self.storage_type == "redis" and self.redis_client:
            return False  # Redis 模式本就持久，防御性跳过

        async with self._get_session_lock(session_id):
            history = self.get_session(session_id)
            if history is not None and history.messages:
                return False  # 已有消息，视为已恢复

            if history is None:
                history = self.create_session(session_id)

            history.messages = messages
            history.last_updated = datetime.now()

            # 回填后按现有压缩策略处理：熵为 high 时压缩较旧消息（含 LLM 摘要），
            # 低熵保持完整。在锁内执行，与 add_message 的增量压缩一致，互斥安全。
            if self.entropy_manager:
                await self._maybe_compress_incremental(history)

            if self.storage_type == "redis" and self.redis_client:
                self._save_to_redis(history)

        logger.info(
            f"[MemoryRestore] session={session_id} 回填 {len(messages)} 条消息"
        )
        return True

    async def get_recent_messages(
        self,
        session_id: str,
        limit: Optional[int] = 50
    ) -> List[Dict[str, str]]:
        """
        获取最近的消息（读取时不做压缩，压缩已在写入时完成）

        Args:
            session_id: 会话ID
            limit: 最大消息数；None 时返回全部消息

        Returns:
            消息列表
        """
        history = self.get_session(session_id)
        if history:
            return history.get_recent_messages(limit)
        return []

    async def get_history(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[Dict[str, str]]:
        """
        获取历史对话（OpenAI 格式，用于 Agent Loop）

        Args:
            session_id: 会话ID
            limit: 最大轮数（一轮 = user + assistant）

        Returns:
            消息列表（OpenAI 格式: [{"role": "user", "content": "..."}, ...]）
        """
        # get_recent_messages 已处理熵管理，这里只做格式转换
        messages = await self.get_recent_messages(session_id, limit * 2)  # 每轮2条消息

        # 转换为 OpenAI 格式（只保留 user 和 assistant 消息）
        openai_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
            if msg["role"] in ["user", "assistant"]
        ]

        return openai_messages

    def clear_session(self, session_id: str):
        """
        清空会话

        Args:
            session_id: 会话ID
        """
        if self.storage_type == "memory":
            self.sessions.pop(session_id, None)
            self._session_locks.pop(session_id, None)
        elif self.storage_type == "redis" and self.redis_client:
            key = f"session:{session_id}"
            self.redis_client.delete(key)

        logger.debug(f"Cleared session: {session_id}")

    def _save_to_redis(self, history: ConversationHistory):
        """保存到 Redis（内部方法）"""
        if not self.redis_client:
            return

        try:
            key = f"session:{history.session_id}"
            value = json.dumps(history.to_dict())
            # 设置过期时间
            self.redis_client.setex(key, self.ttl_seconds, value)
        except Exception as e:
            logger.error(f"Failed to save to Redis: {e}")

    def _load_from_redis(self, session_id: str) -> Optional[ConversationHistory]:
        """从 Redis 加载（内部方法）"""
        if not self.redis_client:
            return None

        try:
            key = f"session:{session_id}"
            value = self.redis_client.get(key)

            if value:
                data = json.loads(value)
                return ConversationHistory.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load from Redis: {e}")

        return None

    def get_sub_sessions(self, main_session_id: str) -> List[ConversationHistory]:
        """
        获取主会话下的所有子会话

        Args:
            main_session_id: 主会话 ID

        Returns:
            子会话的 ConversationHistory 列表
        """
        prefix = f"{main_session_id}:"
        if self.storage_type == "memory":
            return [
                h for sid, h in self.sessions.items()
                if sid.startswith(prefix)
            ]
        return []

    def merge_sub_session(
        self,
        main_session_id: str,
        sub_session_id: str,
        summary_text: str,
        role: str = "assistant"
    ):
        """
        将子会话的摘要合并到主会话，然后清除子会话

        Args:
            main_session_id: 主会话 ID
            sub_session_id: 子会话 ID
            summary_text: 合并的摘要文本
            role: 消息角色（默认 assistant）
        """
        # 确保主会话存在
        main_session = self.get_session(main_session_id)
        if main_session is None:
            main_session = self.create_session(main_session_id)

        # 追加摘要到主会话
        main_session.add_message(role, summary_text)

        # 持久化（如果用 Redis）
        if self.storage_type == "redis" and self.redis_client:
            self._save_to_redis(main_session)

        # 清除子会话
        self.clear_session(sub_session_id)

        logger.debug(f"Merged sub-session {sub_session_id} into {main_session_id}")

    def get_all_messages(self, session_id: str) -> List[Dict[str, str]]:
        """
        获取会话的所有消息（含 tool 类型，不做格式过滤）

        Args:
            session_id: 会话 ID

        Returns:
            完整消息列表
        """
        history = self.get_session(session_id)
        if history:
            return list(history.messages)
        return []
