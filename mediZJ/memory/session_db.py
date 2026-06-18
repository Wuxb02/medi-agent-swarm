"""
SQLite 会话数据库管理器

功能：
- 持久化存储多轮会话数据（sessions + messages 表）
- 支持按 session_id 查询完整对话历史
- 支持会话列表、删除等 CRUD 操作
- 使用 WAL 模式提升并发读性能

存储路径：memory/data/sessions.db
"""
import json
import os
import sqlite3
import threading
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
                agent_events       TEXT,
                    -- SSE 事件列表 JSON
                suggestions        TEXT,
                    -- 建议列表 JSON
                disclaimer         TEXT,
                agents_involved    TEXT,
                    -- Agent 列表 JSON
                total_time         REAL DEFAULT 0,
                total_tokens       INTEGER DEFAULT 0,
                subtasks_completed INTEGER DEFAULT 0,
                mode               TEXT,
                citations          TEXT,
                    -- 知识库引用列表 JSON [{index, doc_id, source, ...}]
                FOREIGN KEY (session_id)
                    REFERENCES sessions(session_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_msg_session
                ON messages(session_id, turn_index);
        """)

    @staticmethod
    def _migrate_tables(conn: sqlite3.Connection):
        """数据库迁移：为已有表添加新列"""
        migrations = [
            ("sessions", "parallel_efficiency", "REAL DEFAULT 0"),
            ("sessions", "information_coverage", "REAL DEFAULT 0"),
            ("sessions", "redundancy", "REAL DEFAULT 0"),
            ("messages", "citations", "TEXT"),
        ]
        for table, col, col_type in migrations:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # 列已存在

    def save_turn(
        self,
        session_id: str,
        turn_index: int,
        user_msg: Dict[str, Any],
        assistant_msg: Dict[str, Any],
    ):
        """
        保存一轮对话（user + assistant），事务原子写入

        Args:
            session_id: 会话 ID
            turn_index: 轮次索引（从 0 开始）
            user_msg: 用户消息 {role, content, timestamp}
            assistant_msg: 助手消息 {role, content, timestamp, agent_events,
                suggestions, disclaimer, agents_involved, total_time,
                total_tokens, subtasks_completed, mode}
        """

        def _do_save(conn: sqlite3.Connection):
            now = datetime.now().isoformat()

            # UPSERT session 元数据
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, created_at, updated_at, mode,
                     first_question, total_tokens, message_count, turn_count,
                     parallel_efficiency, information_coverage, redundancy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

            # INSERT user message
            conn.execute(
                """
                INSERT INTO messages
                    (session_id, turn_index, role, content, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_index,
                    "user",
                    user_msg.get("content", ""),
                    user_msg.get("timestamp", now),
                ),
            )

            # INSERT assistant message
            agent_events = assistant_msg.get("agent_events")
            suggestions = assistant_msg.get("suggestions")
            agents_involved = assistant_msg.get("agents_involved")
            citations = assistant_msg.get("citations")

            conn.execute(
                """
                INSERT INTO messages
                    (session_id, turn_index, role, content, timestamp,
                     agent_events, suggestions, disclaimer,
                     agents_involved, total_time, total_tokens,
                     subtasks_completed, mode, citations)
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
                    assistant_msg.get("disclaimer"),
                    json.dumps(agents_involved, ensure_ascii=False)
                    if agents_involved else None,
                    assistant_msg.get("total_time", 0),
                    assistant_msg.get("total_tokens", 0),
                    assistant_msg.get("subtasks_completed", 0),
                    assistant_msg.get("mode"),
                    json.dumps(citations, ensure_ascii=False, default=str)
                    if citations else None,
                ),
            )

        self._execute(_do_save)
        logger.debug(
            f"Saved turn {turn_index} for session {session_id}"
        )

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取完整会话（含所有 messages）

        Returns:
            {session_id, created_at, updated_at, mode, ...,
             messages: [{turn_index, role, content, timestamp, agent_events, ...}, ...]}
            不存在时返回 None
        """

        def _do_get(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
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
                for field in ("agent_events", "suggestions", "agents_involved", "citations"):
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

    def list_sessions(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """列出会话摘要，按 updated_at DESC"""

        def _do_list(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
            rows = conn.execute(
                """
                SELECT * FROM sessions
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

        return self._execute(_do_list)

    def count_sessions(self) -> int:
        """获取会话总数"""

        def _do_count(conn: sqlite3.Connection) -> int:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM sessions").fetchone()
            return row["cnt"] if row else 0

        return self._execute(_do_count)

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其所有 messages（CASCADE）"""

        def _do_delete(conn: sqlite3.Connection) -> bool:
            # 先删 messages（虽然 CASCADE 会处理，但显式更安全）
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
