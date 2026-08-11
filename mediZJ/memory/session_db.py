"""
SQLite 会话数据库管理器

功能：
- 持久化存储多轮会话数据（sessions + messages 表）
- 持久化存储个人健康档案（profiles 表，md 文本整体入库）
- 支持按 session_id 查询完整对话历史
- 支持会话列表、删除等 CRUD 操作
- 使用 WAL 模式提升并发读性能

存储路径：memory/data/sessions.db
"""
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# 默认数据库路径
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__), "data", "sessions.db"
)


class SessionDB:
    """SQLite 会话数据库管理器（线程安全）"""

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """单例模式"""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        if hasattr(self, "_initialized"):
            return

        self.db_path = db_path
        self._local = threading.local()

        # 确保目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 初始化数据库表
        self._execute(self._create_tables)
        self._execute(self._migrate_tables)

        self._initialized = True
        logger.info(f"SessionDB initialized: {db_path}")

    @classmethod
    def reset(cls):
        """重置单例（仅测试使用，生产代码禁止调用）"""
        cls._instance = None

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _execute(self, func, *args, **kwargs):
        """在线程安全的连接上执行操作"""
        conn = self._get_conn()
        try:
            result = func(conn, *args, **kwargs)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _create_tables(conn: sqlite3.Connection):
        """创建数据库表"""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id     TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL DEFAULT 'default',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                mode           TEXT DEFAULT 'single',
                first_question TEXT DEFAULT '',
                total_tokens   INTEGER DEFAULT 0,
                message_count  INTEGER DEFAULT 0,
                turn_count     INTEGER DEFAULT 0,
                parallel_efficiency  REAL DEFAULT 0,
                information_coverage REAL DEFAULT 0,
                redundancy           REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id         TEXT NOT NULL,
                turn_index         INTEGER NOT NULL,
                role               TEXT NOT NULL,
                    -- 'user' | 'assistant'
                content            TEXT NOT NULL,
                timestamp          TEXT NOT NULL,
                images             TEXT,
                    -- 图片 URL 列表 JSON（user 消息专用）
                agent_events       TEXT,
                    -- SSE 事件列表 JSON
                suggestions        TEXT,
                    -- 建议列表 JSON
                agents_involved    TEXT,
                    -- Agent 列表 JSON
                total_time         REAL DEFAULT 0,
                total_tokens       INTEGER DEFAULT 0,
                subtasks_completed INTEGER DEFAULT 0,
                mode               TEXT,
                citations          TEXT,
                trace_id           TEXT,
                    -- 知识库引用列表 JSON [{index, doc_id, source, ...}]
                FOREIGN KEY (session_id)
                    REFERENCES sessions(session_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS profiles (
                user_id    TEXT PRIMARY KEY,
                content    TEXT NOT NULL DEFAULT '',
                    -- 档案正文（原 PERSONAL.md 全文）
                pending    TEXT NOT NULL DEFAULT '',
                    -- 待确认暂存（原 PENDING.md 全文）
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS users (
                user_id             TEXT PRIMARY KEY,
                username            TEXT NOT NULL,
                username_normalized TEXT NOT NULL UNIQUE,
                role                TEXT NOT NULL DEFAULT 'user',
                is_active           INTEGER NOT NULL DEFAULT 1,
                created_at          TEXT NOT NULL,
                last_login_at       TEXT
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash   TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY (user_id)
                    REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS uploads (
                filename      TEXT PRIMARY KEY,
                user_id       TEXT NOT NULL,
                original_name TEXT NOT NULL,
                content_type  TEXT NOT NULL,
                size          INTEGER NOT NULL,
                created_at    TEXT NOT NULL,
                FOREIGN KEY (user_id)
                    REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_msg_session
                ON messages(session_id, turn_index);
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                ON auth_sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_uploads_user
                ON uploads(user_id, created_at);
        """)

        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO users
                (user_id, username, username_normalized, role, is_active,
                 created_at, last_login_at)
            VALUES ('default', 'default', 'default', 'user', 1, ?, NULL)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (now,),
        )

    @staticmethod
    def _migrate_tables(conn: sqlite3.Connection):
        """数据库迁移：为已有表添加新列"""
        migrations = [
            ("sessions", "user_id", "TEXT NOT NULL DEFAULT 'default'"),
            ("sessions", "parallel_efficiency", "REAL DEFAULT 0"),
            ("sessions", "information_coverage", "REAL DEFAULT 0"),
            ("sessions", "redundancy", "REAL DEFAULT 0"),
            ("messages", "citations", "TEXT"),
            ("messages", "images", "TEXT"),
            ("messages", "trace_id", "TEXT"),
        ]
        for table, col, col_type in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # 列已存在

        conn.execute(
            "UPDATE sessions SET user_id = 'default' "
            "WHERE user_id IS NULL OR user_id = ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user "
            "ON sessions(user_id, updated_at)"
        )

    # ========== 用户与登录会话 ==========

    def get_or_create_user(
        self,
        username: str,
        role: str = "user",
    ) -> Dict[str, Any]:
        """按规范化用户名获取用户，不存在时自动创建。"""

        normalized = username.casefold()

        def _do_get_or_create(conn: sqlite3.Connection) -> Dict[str, Any]:
            now = datetime.now().isoformat()
            row = conn.execute(
                "SELECT * FROM users WHERE username_normalized = ?",
                (normalized,),
            ).fetchone()
            if row is None:
                user_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO users
                        (user_id, username, username_normalized, role,
                         is_active, created_at, last_login_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(username_normalized) DO NOTHING
                    """,
                    (user_id, username, normalized, role, now, now),
                )
                conn.execute(
                    """
                    UPDATE OR IGNORE profiles
                    SET user_id = ?
                    WHERE lower(user_id) = ? AND user_id != 'default'
                    """,
                    (user_id, normalized),
                )
                row = conn.execute(
                    "SELECT * FROM users WHERE username_normalized = ?",
                    (normalized,),
                ).fetchone()
            effective_role = "admin" if role == "admin" else row["role"]
            conn.execute(
                """
                UPDATE users
                SET last_login_at = ?, role = ?
                WHERE user_id = ?
                """,
                (now, effective_role, row["user_id"]),
            )
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (row["user_id"],),
            ).fetchone()
            return dict(row)

        return self._execute(_do_get_or_create)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """按用户 ID 查询账号。"""

        def _do_get(conn: sqlite3.Connection):
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None

        return self._execute(_do_get)

    def save_auth_session(
        self,
        token_hash: str,
        user_id: str,
        expires_at: str,
    ) -> None:
        """保存登录令牌哈希。"""

        def _do_save(conn: sqlite3.Connection):
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO auth_sessions
                    (token_hash, user_id, created_at, expires_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, user_id, now, expires_at, now),
            )

        self._execute(_do_save)

    def get_auth_session(self, token_hash: str) -> Optional[Dict[str, Any]]:
        """查询登录会话及其用户信息。"""

        def _do_get(conn: sqlite3.Connection):
            row = conn.execute(
                """
                SELECT a.token_hash, a.user_id, a.expires_at,
                       u.username, u.role, u.is_active
                FROM auth_sessions AS a
                JOIN users AS u ON u.user_id = a.user_id
                WHERE a.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE auth_sessions SET last_seen_at = ? "
                    "WHERE token_hash = ?",
                    (datetime.now().isoformat(), token_hash),
                )
            return dict(row) if row else None

        return self._execute(_do_get)

    def delete_auth_session(self, token_hash: str) -> bool:
        """撤销指定登录会话。"""

        def _do_delete(conn: sqlite3.Connection):
            cursor = conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (token_hash,),
            )
            return cursor.rowcount > 0

        return self._execute(_do_delete)

    def save_upload(
        self,
        filename: str,
        user_id: str,
        original_name: str,
        content_type: str,
        size: int,
    ) -> None:
        """记录上传文件归属。"""

        def _do_save(conn: sqlite3.Connection):
            conn.execute(
                """
                INSERT INTO uploads
                    (filename, user_id, original_name, content_type,
                     size, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    filename,
                    user_id,
                    original_name,
                    content_type,
                    size,
                    datetime.now().isoformat(),
                ),
            )

        self._execute(_do_save)

    def get_upload(self, filename: str) -> Optional[Dict[str, Any]]:
        """查询上传文件元数据。"""

        def _do_get(conn: sqlite3.Connection):
            row = conn.execute(
                "SELECT * FROM uploads WHERE filename = ?",
                (filename,),
            ).fetchone()
            return dict(row) if row else None

        return self._execute(_do_get)

    # ========== 个人健康档案（profiles 表） ==========

    def get_profile(self, user_id: str) -> Optional[Dict[str, str]]:
        """读取用户档案行，不存在时返回 None"""

        def _do_get(conn: sqlite3.Connection):
            row = conn.execute(
                "SELECT content, pending FROM profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            return {"content": row["content"], "pending": row["pending"]}

        return self._execute(_do_get)

    def upsert_profile(
        self,
        user_id: str,
        content: Optional[str] = None,
        pending: Optional[str] = None,
    ):
        """写入用户档案，仅更新传入的非 None 列；行不存在则插入"""

        def _do_upsert(conn: sqlite3.Connection):
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO profiles (user_id, content, pending, updated_at)
                VALUES (?, '', '', ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id, now),
            )
            if content is not None:
                conn.execute(
                    "UPDATE profiles SET content = ?, updated_at = ?"
                    " WHERE user_id = ?",
                    (content, now, user_id),
                )
            if pending is not None:
                conn.execute(
                    "UPDATE profiles SET pending = ?, updated_at = ?"
                    " WHERE user_id = ?",
                    (pending, now, user_id),
                )

        self._execute(_do_upsert)

    def save_turn(
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

        def _do_save(conn: sqlite3.Connection):
            now = datetime.now().isoformat()

            owner = conn.execute(
                "SELECT user_id FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if owner is not None and owner["user_id"] != user_id:
                raise PermissionError("会话不属于当前用户")

            # UPSERT session 元数据
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, user_id, created_at, updated_at, mode,
                     first_question, total_tokens, message_count, turn_count,
                     parallel_efficiency, information_coverage, redundancy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at     = excluded.updated_at,
                    mode           = excluded.mode,
                    total_tokens   = sessions.total_tokens + excluded.total_tokens,
                    message_count  = sessions.message_count + excluded.message_count,
                    turn_count     = sessions.turn_count + 1,
                    parallel_efficiency  = excluded.parallel_efficiency,
                    information_coverage = excluded.information_coverage,
                    redundancy           = excluded.redundancy
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

            persisted_owner = conn.execute(
                "SELECT user_id FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if persisted_owner["user_id"] != user_id:
                raise PermissionError("会话不属于当前用户")

            # INSERT user message
            images_json = json.dumps(user_msg.get("images") or [], ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO messages
                    (session_id, turn_index, role, content, timestamp, images)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_index,
                    "user",
                    user_msg.get("content", ""),
                    user_msg.get("timestamp", now),
                    images_json,
                ),
            )

            # INSERT assistant message
            agent_events = assistant_msg.get("agent_events")
            suggestions = assistant_msg.get("suggestions")
            agents_involved = assistant_msg.get("agents_involved")
            citations = assistant_msg.get("citations")

            assistant_cursor = conn.execute(
                """
                INSERT INTO messages
                    (session_id, turn_index, role, content, timestamp,
                     agent_events, suggestions,
                     agents_involved, total_time, total_tokens,
                     subtasks_completed, mode, citations, trace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_index,
                    "assistant",
                    assistant_msg.get("content", ""),
                    assistant_msg.get("timestamp", now),
                    json.dumps(agent_events, ensure_ascii=False, default=str)
                    if agent_events else None,
                    json.dumps(suggestions, ensure_ascii=False)
                    if suggestions else None,
                    json.dumps(agents_involved, ensure_ascii=False)
                    if agents_involved else None,
                    assistant_msg.get("total_time", 0),
                    assistant_msg.get("total_tokens", 0),
                    assistant_msg.get("subtasks_completed", 0),
                    assistant_msg.get("mode"),
                    json.dumps(citations, ensure_ascii=False, default=str)
                    if citations else None,
                    assistant_msg.get("trace_id"),
                ),
            )
            return {
                "assistant_message_id": str(assistant_cursor.lastrowid),
                "turn_index": turn_index,
            }

        saved = self._execute(_do_save)
        logger.debug(
            f"Saved turn {turn_index} for session {session_id}"
        )
        return saved

    def get_session(
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

        def _do_get(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
            if user_id is None:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM sessions "
                    "WHERE session_id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
            if not row:
                return None

            session = dict(row)

            msg_rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY turn_index, id
                """,
                (session_id,),
            ).fetchall()

            messages = []
            for mr in msg_rows:
                msg = dict(mr)
                # 反序列化 JSON 字段
                for field in ("agent_events", "suggestions", "agents_involved", "citations", "images"):
                    val = msg.get(field)
                    if val and isinstance(val, str):
                        try:
                            msg[field] = json.loads(val)
                        except (json.JSONDecodeError, TypeError):
                            pass
                messages.append(msg)

            session["messages"] = messages
            return session

        return self._execute(_do_get)

    def get_turn_count(self, session_id: str) -> int:
        """获取当前会话的轮次数量"""

        def _do_count(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                """
                SELECT MAX(turn_index) as max_turn
                FROM messages WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if row and row["max_turn"] is not None:
                return row["max_turn"] + 1
            return 0

        return self._execute(_do_count)

    def get_recent_turns(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        limit: Optional[int] = 10,
    ) -> List[Dict[str, Any]]:
        """获取最近 N 轮消息（按时间正序返回），用于会话恢复回填短期记忆

        limit 为 None 时返回全部消息。仅反序列化 images 列
        （恢复上下文只需要 role/content/timestamp），避免反序列化大 JSON 字段。
        """

        def _do_get(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
            if user_id is None:
                owner_ok = conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone() is not None
            else:
                owner_ok = conn.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone() is not None
            if not owner_ok:
                return []

            sql = """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY turn_index DESC, id DESC
            """
            params: tuple = (session_id,)
            if limit is not None:
                sql += " LIMIT ?"
                params = (session_id, limit * 2)

            rows = conn.execute(sql, params).fetchall()

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

        return self._execute(_do_get)

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出会话摘要，按 updated_at DESC"""

        def _do_list(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
            if user_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM sessions
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM sessions
                    WHERE user_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (user_id, limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

        return self._execute(_do_list)

    def count_sessions(self, user_id: Optional[str] = None) -> int:
        """获取会话总数"""

        def _do_count(conn: sqlite3.Connection) -> int:
            if user_id is None:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM sessions"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM sessions WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            return row["cnt"] if row else 0

        return self._execute(_do_count)

    def delete_session(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> bool:
        """删除会话及其所有 messages（CASCADE）"""

        def _do_delete(conn: sqlite3.Connection) -> bool:
            if user_id is not None:
                owned = conn.execute(
                    "SELECT 1 FROM sessions "
                    "WHERE session_id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
                if owned is None:
                    return False
            conn.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session_id,),
            )
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0

        result = self._execute(_do_delete)
        if result:
            logger.debug(f"Deleted session from DB: {session_id}")
        return result
