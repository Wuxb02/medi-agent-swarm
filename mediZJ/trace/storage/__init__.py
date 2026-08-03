"""Trace SQLite 存储后端"""
import json
import os
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "memory", "data", "sessions.db"
)


def _safe_asdict(obj):
    """安全转换为 dict，兼容 dataclass 和非 dataclass 对象"""
    if obj is None:
        return {}
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, '__dict__'):
        return vars(obj)
    return {"_value": str(obj)}


class TraceSqliteStorage:
    """Trace 数据的 SQLite 存储（复用 sessions.db）

    traces 表存储完整嵌套树（tree_json），spans 表存储扁平行用于查询。
    """

    _instance: Optional["TraceSqliteStorage"] = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        if hasattr(self, "_initialized"):
            return
        self.db_path = str(Path(db_path).resolve())
        self._local = threading.local()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._execute(self._create_tables)
        self._initialized = True
        logger.info(f"TraceSqliteStorage initialized: {self.db_path}")

    @classmethod
    def reset(cls):
        """重置单例（测试用）"""
        cls._instance = None

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _execute(self, func, *args, **kwargs):
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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS traces (
                trace_id         TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL,
                user_id          TEXT NOT NULL DEFAULT 'default',
                status           TEXT DEFAULT 'ok',
                start_time       TEXT NOT NULL,
                end_time         TEXT,
                duration_ms      REAL,
                mode             TEXT DEFAULT '',
                total_tokens     INTEGER DEFAULT 0,
                agents_involved  TEXT,
                span_count       INTEGER DEFAULT 0,
                question_summary TEXT DEFAULT '',
                tree_json        TEXT NOT NULL,
                created_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS spans (
                id            TEXT PRIMARY KEY,
                trace_id      TEXT NOT NULL,
                parent_id     TEXT,
                span_type     TEXT NOT NULL,
                name          TEXT DEFAULT '',
                status        TEXT DEFAULT 'ok',
                start_time    TEXT NOT NULL,
                end_time      TEXT,
                duration_ms   REAL,
                error_message TEXT,
                llm_attrs     TEXT,
                tool_attrs    TEXT,
                agent_attrs   TEXT,
                FOREIGN KEY (trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_trace_spans
                ON spans(trace_id);
            CREATE INDEX IF NOT EXISTS idx_span_type
                ON spans(span_type);
            CREATE INDEX IF NOT EXISTS idx_span_parent
                ON spans(parent_id);
            CREATE INDEX IF NOT EXISTS idx_trace_session
                ON traces(session_id);
        """)
        try:
            conn.execute(
                "ALTER TABLE traces ADD COLUMN "
                "user_id TEXT NOT NULL DEFAULT 'default'"
            )
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trace_user "
            "ON traces(user_id, start_time)"
        )

    def save(self, root_span, flat_spans: List):
        """保存 trace：写入 traces 表（树 JSON）+ spans 表（扁平行）

        Args:
            root_span: 根 Span 对象（已构建树）
            flat_spans: 扁平 Span 列表
        """
        def _do_save(conn: sqlite3.Connection):
            now = datetime.now().isoformat()
            # 使用 spans 的 trace_id 作为表主键（= session_id）
            trace_id = root_span.trace_id or root_span.id

            # 从根 span 提取 trace 属性
            trace_attrs = root_span.trace_attrs
            session_id = trace_attrs.session_id if trace_attrs else trace_id
            user_id = trace_attrs.user_id if trace_attrs else "default"
            mode = trace_attrs.mode if trace_attrs else ""
            question_summary = trace_attrs.question_summary if trace_attrs else ""
            agents_involved = trace_attrs.agents_involved if trace_attrs else []
            total_tokens = trace_attrs.total_tokens if trace_attrs else 0

            # 写入 traces 表（先写，满足 FK 约束）
            conn.execute(
                """INSERT OR REPLACE INTO traces
                   (trace_id, session_id, user_id, status, start_time, end_time,
                    duration_ms, mode, total_tokens, agents_involved,
                    span_count, question_summary, tree_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trace_id,
                    session_id,
                    user_id,
                    root_span.status.value,
                    root_span.timing.start_time.isoformat(),
                    root_span.timing.end_time.isoformat() if root_span.timing.end_time else None,
                    root_span.timing.duration_ms,
                    mode,
                    total_tokens,
                    json.dumps(agents_involved, ensure_ascii=False),
                    len(flat_spans),
                    question_summary,
                    json.dumps(self._span_to_tree_dict(root_span), ensure_ascii=False, default=str),
                    now,
                ),
            )

            # 写入 spans 表（批量插入）
            for span in flat_spans:
                conn.execute(
                    """INSERT OR REPLACE INTO spans
                       (id, trace_id, parent_id, span_type, name, status,
                        start_time, end_time, duration_ms, error_message,
                        llm_attrs, tool_attrs, agent_attrs)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        span.id,
                        span.trace_id,
                        span.parent_id,
                        span.span_type.value,
                        span.name,
                        span.status.value,
                        span.timing.start_time.isoformat(),
                        span.timing.end_time.isoformat() if span.timing.end_time else None,
                        span.timing.duration_ms,
                        span.error_message,
                        json.dumps(_safe_asdict(span.llm_attrs), ensure_ascii=False) if span.llm_attrs else None,
                        json.dumps(_safe_asdict(span.tool_attrs), ensure_ascii=False) if span.tool_attrs else None,
                        json.dumps(_safe_asdict(span.agent_attrs), ensure_ascii=False) if span.agent_attrs else None,
                    ),
                )

        self._execute(_do_save)

    def get_trace(
        self,
        trace_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取完整 trace 树（从 tree_json）"""
        def _do_get(conn: sqlite3.Connection):
            if user_id is None:
                row = conn.execute(
                    "SELECT tree_json FROM traces WHERE trace_id = ?",
                    (trace_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT tree_json FROM traces "
                    "WHERE trace_id = ? AND user_id = ?",
                    (trace_id, user_id),
                ).fetchone()
            return json.loads(row["tree_json"]) if row else None
        return self._execute(_do_get)

    def get_flat_spans(
        self,
        trace_id: str,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取扁平 span 列表"""
        def _do_get(conn: sqlite3.Connection):
            if user_id is not None:
                owner = conn.execute(
                    "SELECT 1 FROM traces "
                    "WHERE trace_id = ? AND user_id = ?",
                    (trace_id, user_id),
                ).fetchone()
                if owner is None:
                    return []
            rows = conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time",
                (trace_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        return self._execute(_do_get)

    def list_traces(
        self,
        limit: int = 50,
        offset: int = 0,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出最近 trace"""
        def _do_list(conn: sqlite3.Connection):
            if session_id and user_id:
                rows = conn.execute(
                    """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces WHERE session_id = ? AND user_id = ?
                       ORDER BY start_time DESC LIMIT ? OFFSET ?""",
                    (session_id, user_id, limit, offset),
                ).fetchall()
            elif session_id:
                rows = conn.execute(
                    """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces WHERE session_id = ?
                       ORDER BY start_time DESC LIMIT ? OFFSET ?""",
                    (session_id, limit, offset),
                ).fetchall()
            elif user_id:
                rows = conn.execute(
                    """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces WHERE user_id = ?
                       ORDER BY start_time DESC LIMIT ? OFFSET ?""",
                    (user_id, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces ORDER BY start_time DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["agents_involved"] = json.loads(d["agents_involved"]) if d["agents_involved"] else []
                results.append(d)
            return results
        return self._execute(_do_list)

    def count_traces(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """统计 trace 总数"""
        def _do_count(conn: sqlite3.Connection):
            if session_id and user_id:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM traces "
                    "WHERE session_id = ? AND user_id = ?",
                    (session_id, user_id),
                ).fetchone()
            elif session_id:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM traces WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            elif user_id:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM traces WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) as cnt FROM traces").fetchone()
            return row["cnt"]
        return self._execute(_do_count)

    def delete_trace(self, trace_id: str) -> bool:
        """删除指定 trace（spans 通过 FK ON DELETE CASCADE 自动删除）"""
        def _do_delete(conn: sqlite3.Connection):
            cursor = conn.execute(
                "DELETE FROM traces WHERE trace_id = ?", (trace_id,)
            )
            return cursor.rowcount > 0
        return self._execute(_do_delete)

    @staticmethod
    def _span_to_tree_dict(span) -> dict:
        """递归将 span 树转为嵌套字典"""
        d: Dict[str, Any] = {
            "id": span.id,
            "trace_id": span.trace_id,
            "span_type": span.span_type.value,
            "name": span.name,
            "status": span.status.value,
            "timing": {
                "start_time": span.timing.start_time.isoformat(),
                "end_time": span.timing.end_time.isoformat() if span.timing.end_time else None,
                "duration_ms": span.timing.duration_ms,
            },
        }
        if span.error_message:
            d["error_message"] = span.error_message
        if span.trace_attrs:
            d["trace_attrs"] = _safe_asdict(span.trace_attrs)
        if span.agent_attrs:
            d["agent_attrs"] = _safe_asdict(span.agent_attrs)
        if span.llm_attrs:
            d["llm_attrs"] = _safe_asdict(span.llm_attrs)
        if span.tool_attrs:
            d["tool_attrs"] = _safe_asdict(span.tool_attrs)
        if span.children:
            d["children"] = [
                TraceSqliteStorage._span_to_tree_dict(c) for c in span.children
            ]
        return d
