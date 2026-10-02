"""Trace MySQL 存储后端"""

from mediZJ.infrastructure.database import Connection, execute
import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _loads_agents(value: Any) -> List[str]:
    """安全解析 agents_involved 列，容忍历史脏数据/非法 JSON"""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _safe_asdict(obj):
    """安全转换为 dict，兼容 dataclass 和非 dataclass 对象"""
    if obj is None:
        return {}
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return vars(obj)
    return {"_value": str(obj)}


class TraceStorage:
    """Trace 数据的 MySQL 存储（复用 MySQL）

    traces 表存储完整嵌套树（tree_json），spans 表存储扁平行用于查询。
    """

    _instance: Optional["TraceStorage"] = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
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
        """重置单例（测试用）"""
        cls._instance = None

    async def _execute(self, func, *args, **kwargs):
        return await execute(func, *args, **kwargs)

    async def save(self, root_span, flat_spans: List):
        """保存 trace：写入 traces 表（树 JSON）+ spans 表（扁平行）

        Args:
            root_span: 根 Span 对象（已构建树）
            flat_spans: 扁平 Span 列表
        """

        async def _do_save(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()
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
            (
                await conn.execute(
                    """INSERT INTO traces
                   (trace_id, session_id, user_id, status, start_time, end_time,
                    duration_ms, mode, total_tokens, agents_involved,
                    span_count, question_summary, tree_json, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE trace_id = VALUES(trace_id), session_id = VALUES(session_id), user_id = VALUES(user_id), status = VALUES(status), start_time = VALUES(start_time), end_time = VALUES(end_time), duration_ms = VALUES(duration_ms), mode = VALUES(mode), total_tokens = VALUES(total_tokens), agents_involved = VALUES(agents_involved), span_count = VALUES(span_count), question_summary = VALUES(question_summary), tree_json = VALUES(tree_json), created_at = VALUES(created_at)""",
                    (
                        trace_id,
                        session_id,
                        user_id,
                        root_span.status.value,
                        root_span.timing.start_time.isoformat(),
                        root_span.timing.end_time.isoformat()
                        if root_span.timing.end_time
                        else None,
                        root_span.timing.duration_ms,
                        mode,
                        total_tokens,
                        json.dumps(agents_involved, ensure_ascii=False),
                        len(flat_spans),
                        question_summary,
                        json.dumps(
                            self._span_to_tree_dict(root_span),
                            ensure_ascii=False,
                            default=str,
                        ),
                        now,
                    ),
                )
            )

            # 写入 spans 表（批量插入）
            for span in flat_spans:
                (
                    await conn.execute(
                        """INSERT INTO spans
                       (id, trace_id, parent_id, span_type, name, status,
                        start_time, end_time, duration_ms, error_message,
                        llm_attrs, tool_attrs, agent_attrs)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE id = VALUES(id), trace_id = VALUES(trace_id), parent_id = VALUES(parent_id), span_type = VALUES(span_type), name = VALUES(name), status = VALUES(status), start_time = VALUES(start_time), end_time = VALUES(end_time), duration_ms = VALUES(duration_ms), error_message = VALUES(error_message), llm_attrs = VALUES(llm_attrs), tool_attrs = VALUES(tool_attrs), agent_attrs = VALUES(agent_attrs)""",
                        (
                            span.id,
                            span.trace_id,
                            span.parent_id,
                            span.span_type.value,
                            span.name,
                            span.status.value,
                            span.timing.start_time.isoformat(),
                            span.timing.end_time.isoformat()
                            if span.timing.end_time
                            else None,
                            span.timing.duration_ms,
                            span.error_message,
                            json.dumps(_safe_asdict(span.llm_attrs), ensure_ascii=False)
                            if span.llm_attrs
                            else None,
                            json.dumps(
                                _safe_asdict(span.tool_attrs), ensure_ascii=False
                            )
                            if span.tool_attrs
                            else None,
                            json.dumps(
                                _safe_asdict(span.agent_attrs), ensure_ascii=False
                            )
                            if span.agent_attrs
                            else None,
                        ),
                    )
                )

        (await self._execute(_do_save))

    async def get_trace(
        self,
        trace_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取完整 trace 树（从 tree_json）"""

        async def _do_get(conn: Connection):
            if user_id is None:
                row = (
                    await conn.execute(
                        "SELECT tree_json FROM traces WHERE trace_id = %s",
                        (trace_id,),
                    )
                ).fetchone()
            else:
                row = (
                    await conn.execute(
                        "SELECT tree_json FROM traces WHERE trace_id = %s AND user_id = %s",
                        (trace_id, user_id),
                    )
                ).fetchone()
            return json.loads(row["tree_json"]) if row else None

        return await self._execute(_do_get)

    async def get_flat_spans(
        self,
        trace_id: str,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取扁平 span 列表"""

        async def _do_get(conn: Connection):
            if user_id is not None:
                owner = (
                    await conn.execute(
                        "SELECT 1 FROM traces WHERE trace_id = %s AND user_id = %s",
                        (trace_id, user_id),
                    )
                ).fetchone()
                if owner is None:
                    return []
            rows = (
                await conn.execute(
                    "SELECT * FROM spans WHERE trace_id = %s ORDER BY start_time",
                    (trace_id,),
                )
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._execute(_do_get)

    async def list_traces(
        self,
        limit: int = 50,
        offset: int = 0,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出最近 trace"""

        async def _do_list(conn: Connection):
            if session_id and user_id:
                rows = (
                    await conn.execute(
                        """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces WHERE session_id = %s AND user_id = %s
                       ORDER BY start_time DESC LIMIT %s OFFSET %s""",
                        (session_id, user_id, limit, offset),
                    )
                ).fetchall()
            elif session_id:
                rows = (
                    await conn.execute(
                        """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces WHERE session_id = %s
                       ORDER BY start_time DESC LIMIT %s OFFSET %s""",
                        (session_id, limit, offset),
                    )
                ).fetchall()
            elif user_id:
                rows = (
                    await conn.execute(
                        """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces WHERE user_id = %s
                       ORDER BY start_time DESC LIMIT %s OFFSET %s""",
                        (user_id, limit, offset),
                    )
                ).fetchall()
            else:
                rows = (
                    await conn.execute(
                        """SELECT trace_id, session_id, status, start_time, duration_ms,
                              mode, total_tokens, agents_involved, span_count,
                              question_summary
                       FROM traces ORDER BY start_time DESC LIMIT %s OFFSET %s""",
                        (limit, offset),
                    )
                ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["agents_involved"] = _loads_agents(d["agents_involved"])
                results.append(d)
            return results

        return await self._execute(_do_list)

    async def count_traces(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """统计 trace 总数"""

        async def _do_count(conn: Connection):
            if session_id and user_id:
                row = (
                    await conn.execute(
                        "SELECT COUNT(*) as cnt FROM traces WHERE session_id = %s AND user_id = %s",
                        (session_id, user_id),
                    )
                ).fetchone()
            elif session_id:
                row = (
                    await conn.execute(
                        "SELECT COUNT(*) as cnt FROM traces WHERE session_id = %s",
                        (session_id,),
                    )
                ).fetchone()
            elif user_id:
                row = (
                    await conn.execute(
                        "SELECT COUNT(*) as cnt FROM traces WHERE user_id = %s",
                        (user_id,),
                    )
                ).fetchone()
            else:
                row = (
                    await conn.execute("SELECT COUNT(*) as cnt FROM traces")
                ).fetchone()
            return row["cnt"]

        return await self._execute(_do_count)

    async def delete_trace(self, trace_id: str) -> bool:
        """删除指定 trace（spans 通过 FK ON DELETE CASCADE 自动删除）"""

        async def _do_delete(conn: Connection):
            cursor = await conn.execute(
                "DELETE FROM traces WHERE trace_id = %s", (trace_id,)
            )
            return cursor.rowcount > 0

        return await self._execute(_do_delete)

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
                "end_time": span.timing.end_time.isoformat()
                if span.timing.end_time
                else None,
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
            d["children"] = [TraceStorage._span_to_tree_dict(c) for c in span.children]
        return d
