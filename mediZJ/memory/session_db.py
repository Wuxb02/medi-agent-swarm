"""
MySQL 会话数据库管理器

功能：
- 持久化存储多轮会话数据（sessions + messages 表）
- 持久化存储个人健康档案（profiles 表，md 文本整体入库）
- 支持按 session_id 查询完整对话历史
- 支持会话列表、删除等 CRUD 操作
- 使用事务和行锁保障跨实例一致性

存储：MySQL 服务端
"""

from mediZJ.infrastructure.database import Connection, execute
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger


class SessionDB:
    """MySQL 会话数据库管理器（线程安全）"""

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """单例模式"""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """存储实例不在构造阶段访问数据库。"""
        self._initialized = True

    @classmethod
    def reset(cls):
        """重置单例（仅测试使用，生产代码禁止调用）"""
        cls._instance = None

    async def _execute(self, func, *args, **kwargs):
        return await execute(func, *args, **kwargs)

    # ========== 用户与登录会话 ==========

    async def get_or_create_user(
        self,
        username: str,
        role: str = "user",
    ) -> Dict[str, Any]:
        """按规范化用户名获取用户，不存在时自动创建。"""

        normalized = username.casefold()

        async def _do_get_or_create(conn: Connection) -> Dict[str, Any]:
            now = datetime.now(timezone.utc).isoformat()
            row = (
                await conn.execute(
                    "SELECT * FROM users WHERE username_normalized = %s",
                    (normalized,),
                )
            ).fetchone()
            if row is None:
                user_id = str(uuid.uuid4())
                (
                    await conn.execute(
                        """
                    INSERT INTO users
                        (user_id, username, username_normalized, role,
                         is_active, created_at, last_login_at)
                    VALUES (%s, %s, %s, %s, 1, %s, %s)
                    ON DUPLICATE KEY UPDATE username_normalized = username_normalized
                    """,
                        (user_id, username, normalized, role, now, now),
                    )
                )
                row = (
                    await conn.execute(
                        "SELECT * FROM users WHERE username_normalized = %s",
                        (normalized,),
                    )
                ).fetchone()
            effective_role = "admin" if role == "admin" else row["role"]
            (
                await conn.execute(
                    """
                UPDATE users
                SET last_login_at = %s, role = %s
                WHERE user_id = %s
                """,
                    (now, effective_role, row["user_id"]),
                )
            )
            row = (
                await conn.execute(
                    "SELECT * FROM users WHERE user_id = %s",
                    (row["user_id"],),
                )
            ).fetchone()
            return dict(row)

        return await self._execute(_do_get_or_create)

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """按用户 ID 查询账号。"""

        async def _do_get(conn: Connection):
            row = (
                await conn.execute(
                    "SELECT * FROM users WHERE user_id = %s",
                    (user_id,),
                )
            ).fetchone()
            return dict(row) if row else None

        return await self._execute(_do_get)

    async def save_auth_session(
        self,
        token_hash: str,
        user_id: str,
        expires_at: str,
    ) -> None:
        """保存登录令牌哈希。"""

        async def _do_save(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()
            (
                await conn.execute(
                    """
                INSERT INTO auth_sessions
                    (token_hash, user_id, created_at, expires_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                    (token_hash, user_id, now, expires_at, now),
                )
            )

        (await self._execute(_do_save))

    async def get_auth_session(self, token_hash: str) -> Optional[Dict[str, Any]]:
        """查询登录会话及其用户信息。"""

        async def _do_get(conn: Connection):
            row = (
                await conn.execute(
                    """
                SELECT a.token_hash, a.user_id, a.expires_at,
                       u.username, u.role, u.is_active
                FROM auth_sessions AS a
                JOIN users AS u ON u.user_id = a.user_id
                WHERE a.token_hash = %s
                """,
                    (token_hash,),
                )
            ).fetchone()
            if row:
                (
                    await conn.execute(
                        "UPDATE auth_sessions SET last_seen_at = %s WHERE token_hash = %s",
                        (datetime.now(timezone.utc).isoformat(), token_hash),
                    )
                )
            return dict(row) if row else None

        return await self._execute(_do_get)

    async def delete_auth_session(self, token_hash: str) -> bool:
        """撤销指定登录会话。"""

        async def _do_delete(conn: Connection):
            cursor = await conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash = %s",
                (token_hash,),
            )
            return cursor.rowcount > 0

        return await self._execute(_do_delete)

    async def save_upload(
        self,
        filename: str,
        user_id: str,
        original_name: str,
        content_type: str,
        size: int,
    ) -> None:
        """记录上传文件归属。"""

        async def _do_save(conn: Connection):
            (
                await conn.execute(
                    """
                INSERT INTO uploads
                    (filename, user_id, original_name, content_type,
                     size, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                    (
                        filename,
                        user_id,
                        original_name,
                        content_type,
                        size,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            )

        (await self._execute(_do_save))

    async def get_upload(self, filename: str) -> Optional[Dict[str, Any]]:
        """查询上传文件元数据。"""

        async def _do_get(conn: Connection):
            row = (
                await conn.execute(
                    "SELECT * FROM uploads WHERE filename = %s",
                    (filename,),
                )
            ).fetchone()
            return dict(row) if row else None

        return await self._execute(_do_get)

    # ========== 个人健康档案（profiles 表） ==========

    async def get_profile(self, user_id: str) -> Optional[Dict[str, str]]:
        """读取用户档案行，不存在时返回 None"""

        async def _do_get(conn: Connection):
            row = (
                await conn.execute(
                    "SELECT content, pending FROM profiles WHERE user_id = %s",
                    (user_id,),
                )
            ).fetchone()
            if row is None:
                return None
            return {"content": row["content"], "pending": row["pending"]}

        return await self._execute(_do_get)

    async def upsert_profile(
        self,
        user_id: str,
        content: Optional[str] = None,
        pending: Optional[str] = None,
    ):
        """写入用户档案，仅更新传入的非 None 列；行不存在则插入"""

        async def _do_upsert(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()
            (
                await conn.execute(
                    """
                INSERT INTO profiles (user_id, content, pending, updated_at)
                VALUES (%s, '', '', %s)
                ON DUPLICATE KEY UPDATE user_id = user_id
                """,
                    (user_id, now),
                )
            )
            if content is not None:
                (
                    await conn.execute(
                        "UPDATE profiles SET content = %s, updated_at = %s WHERE user_id = %s",
                        (content, now, user_id),
                    )
                )
            if pending is not None:
                (
                    await conn.execute(
                        "UPDATE profiles SET pending = %s, updated_at = %s WHERE user_id = %s",
                        (pending, now, user_id),
                    )
                )

        (await self._execute(_do_upsert))

    async def save_turn(
        self,
        session_id: str,
        turn_index: int,
        user_msg: Dict[str, Any],
        assistant_msg: Dict[str, Any],
        user_id: str = "default",
    ):
        """
        保存一轮对话（user + assistant），事务原子写入

        Args:
            session_id: 会话 ID
            turn_index: 轮次索引（从 0 开始）
            user_msg: 用户消息 {role, content, timestamp}
            assistant_msg: 助手消息 {role, content, timestamp, agent_events,
                suggestions, agents_involved, total_time,
                total_tokens, subtasks_completed, mode}
        """

        async def _do_save(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()

            owner = (
                await conn.execute(
                    "SELECT user_id FROM sessions WHERE session_id = %s",
                    (session_id,),
                )
            ).fetchone()
            if owner is not None and owner["user_id"] != user_id:
                raise PermissionError("会话不属于当前用户")

            # UPSERT session 元数据
            (
                await conn.execute(
                    """
                INSERT INTO sessions
                    (session_id, user_id, created_at, updated_at, mode,
                     first_question, total_tokens, message_count, turn_count,
                     parallel_efficiency, information_coverage, redundancy)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    updated_at     = VALUES(updated_at),
                    mode           = VALUES(mode),
                    total_tokens   = sessions.total_tokens + VALUES(total_tokens),
                    message_count  = sessions.message_count + VALUES(message_count),
                    turn_count     = sessions.turn_count + 1,
                    parallel_efficiency  = VALUES(parallel_efficiency),
                    information_coverage = VALUES(information_coverage),
                    redundancy           = VALUES(redundancy)
                """,
                    (
                        session_id,
                        user_id,
                        user_msg.get("timestamp", now),
                        now,
                        assistant_msg.get("mode", "single"),
                        user_msg.get("content", "")[:200],
                        assistant_msg.get("total_tokens", 0),
                        2,  # 每轮 2 条消息
                        1,
                        assistant_msg.get("parallel_efficiency", 0),
                        assistant_msg.get("information_coverage", 0),
                        assistant_msg.get("redundancy", 0),
                    ),
                )
            )

            persisted_owner = (
                await conn.execute(
                    "SELECT user_id FROM sessions WHERE session_id = %s",
                    (session_id,),
                )
            ).fetchone()
            if persisted_owner["user_id"] != user_id:
                raise PermissionError("会话不属于当前用户")

            turn_index_row = (
                await conn.execute(
                    "SELECT turn_count FROM sessions WHERE session_id=%s FOR UPDATE",
                    (session_id,),
                )
            ).fetchone()
            actual_turn_index = turn_index_row["turn_count"] - 1
            # INSERT user message
            images_json = json.dumps(user_msg.get("images") or [], ensure_ascii=False)
            (
                await conn.execute(
                    """
                INSERT INTO messages
                    (session_id, turn_index, role, content, timestamp, images)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                    (
                        session_id,
                        actual_turn_index,
                        "user",
                        user_msg.get("content", ""),
                        user_msg.get("timestamp", now),
                        images_json,
                    ),
                )
            )

            # INSERT assistant message
            agent_events = assistant_msg.get("agent_events")
            suggestions = assistant_msg.get("suggestions")
            agents_involved = assistant_msg.get("agents_involved")
            citations = assistant_msg.get("citations")

            assistant_cursor = await conn.execute(
                """
                INSERT INTO messages
                    (session_id, turn_index, role, content, timestamp,
                     agent_events, suggestions,
                     agents_involved, total_time, total_tokens,
                     subtasks_completed, mode, citations, trace_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    actual_turn_index,
                    "assistant",
                    assistant_msg.get("content", ""),
                    assistant_msg.get("timestamp", now),
                    json.dumps(agent_events, ensure_ascii=False, default=str)
                    if agent_events
                    else None,
                    json.dumps(suggestions, ensure_ascii=False)
                    if suggestions
                    else None,
                    json.dumps(agents_involved, ensure_ascii=False)
                    if agents_involved
                    else None,
                    assistant_msg.get("total_time", 0),
                    assistant_msg.get("total_tokens", 0),
                    assistant_msg.get("subtasks_completed", 0),
                    assistant_msg.get("mode"),
                    json.dumps(citations, ensure_ascii=False, default=str)
                    if citations
                    else None,
                    assistant_msg.get("trace_id"),
                ),
            )
            return {
                "assistant_message_id": str(assistant_cursor.lastrowid),
                "turn_index": actual_turn_index,
            }

        saved = await self._execute(_do_save)
        logger.debug(f"Saved turn {turn_index} for session {session_id}")
        return saved

    async def get_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        获取完整会话（含所有 messages）

        Returns:
            {session_id, created_at, updated_at, mode, ...,
             messages: [{turn_index, role, content, timestamp, agent_events, ...}, ...]}
            不存在时返回 None
        """

        async def _do_get(conn: Connection) -> Optional[Dict[str, Any]]:
            if user_id is None:
                row = (
                    await conn.execute(
                        "SELECT * FROM sessions WHERE session_id = %s",
                        (session_id,),
                    )
                ).fetchone()
            else:
                row = (
                    await conn.execute(
                        "SELECT * FROM sessions WHERE session_id = %s AND user_id = %s",
                        (session_id, user_id),
                    )
                ).fetchone()
            if not row:
                return None

            session = dict(row)

            msg_rows = (
                await conn.execute(
                    """
                SELECT * FROM messages
                WHERE session_id = %s
                ORDER BY turn_index, id
                """,
                    (session_id,),
                )
            ).fetchall()

            messages = []
            for mr in msg_rows:
                msg = dict(mr)
                # 反序列化 JSON 字段
                for field in (
                    "agent_events",
                    "suggestions",
                    "agents_involved",
                    "citations",
                    "images",
                ):
                    val = msg.get(field)
                    if val and isinstance(val, str):
                        try:
                            msg[field] = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            pass
                messages.append(msg)

            session["messages"] = messages
            return session

        return await self._execute(_do_get)

    async def get_turn_count(self, session_id: str) -> int:
        """获取当前会话的轮次数量"""

        async def _do_count(conn: Connection) -> int:
            row = (
                await conn.execute(
                    """
                SELECT MAX(turn_index) as max_turn
                FROM messages WHERE session_id = %s
                """,
                    (session_id,),
                )
            ).fetchone()
            if row and row["max_turn"] is not None:
                return row["max_turn"] + 1
            return 0

        return await self._execute(_do_count)

    async def get_recent_turns(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        limit: Optional[int] = 10,
    ) -> List[Dict[str, Any]]:
        """获取最近 N 轮消息（按时间正序返回），用于会话恢复回填短期记忆

        limit 为 None 时返回全部消息。仅反序列化 images 列
        （恢复上下文只需要 role/content/timestamp），避免反序列化大 JSON 字段。
        """

        async def _do_get(conn: Connection) -> List[Dict[str, Any]]:
            if user_id is None:
                owner_ok = (
                    await conn.execute(
                        "SELECT 1 FROM sessions WHERE session_id = %s",
                        (session_id,),
                    )
                ).fetchone() is not None
            else:
                owner_ok = (
                    await conn.execute(
                        "SELECT 1 FROM sessions WHERE session_id = %s AND user_id = %s",
                        (session_id, user_id),
                    )
                ).fetchone() is not None
            if not owner_ok:
                return []

            sql = """
                SELECT * FROM messages
                WHERE session_id = %s
                ORDER BY turn_index DESC, id DESC
            """
            params: tuple = (session_id,)
            if limit is not None:
                sql += " LIMIT %s"
                params = (session_id, limit * 2)

            rows = (await conn.execute(sql, params)).fetchall()

            messages = []
            for mr in reversed(rows):  # 逆序回正：旧 → 新
                msg = dict(mr)
                images = msg.get("images")
                if images and isinstance(images, str):
                    try:
                        msg["images"] = json.loads(images)
                    except (json.JSONDecodeError, TypeError):
                        pass
                messages.append(msg)
            return messages

        return await self._execute(_do_get)

    async def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出会话摘要，按 updated_at DESC"""

        async def _do_list(conn: Connection) -> List[Dict[str, Any]]:
            if user_id is None:
                rows = (
                    await conn.execute(
                        """
                    SELECT * FROM sessions
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                        (limit, offset),
                    )
                ).fetchall()
            else:
                rows = (
                    await conn.execute(
                        """
                    SELECT * FROM sessions
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                        (user_id, limit, offset),
                    )
                ).fetchall()
            return [dict(r) for r in rows]

        return await self._execute(_do_list)

    async def count_sessions(self, user_id: Optional[str] = None) -> int:
        """获取会话总数"""

        async def _do_count(conn: Connection) -> int:
            if user_id is None:
                row = (
                    await conn.execute("SELECT COUNT(*) AS cnt FROM sessions")
                ).fetchone()
            else:
                row = (
                    await conn.execute(
                        "SELECT COUNT(*) AS cnt FROM sessions WHERE user_id = %s",
                        (user_id,),
                    )
                ).fetchone()
            return row["cnt"] if row else 0

        return await self._execute(_do_count)

    async def delete_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> bool:
        """删除会话及其所有 messages（CASCADE）"""

        async def _do_delete(conn: Connection) -> bool:
            if user_id is not None:
                owned = (
                    await conn.execute(
                        "SELECT 1 FROM sessions WHERE session_id = %s AND user_id = %s",
                        (session_id, user_id),
                    )
                ).fetchone()
                if owned is None:
                    return False
            (
                await conn.execute(
                    "DELETE FROM messages WHERE session_id = %s",
                    (session_id,),
                )
            )
            cursor = await conn.execute(
                "DELETE FROM sessions WHERE session_id = %s",
                (session_id,),
            )
            return cursor.rowcount > 0

        result = await self._execute(_do_delete)
        if result:
            logger.debug(f"Deleted session from DB: {session_id}")
        return result
