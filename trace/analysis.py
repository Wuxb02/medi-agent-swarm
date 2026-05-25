"""Trace 聚合分析引擎"""
import json
import sqlite3
import threading
from typing import Any, Dict, List, Optional


class TraceAnalyzer:
    """基于 SQLite spans 表的 trace 聚合分析"""

    _instance: Optional["TraceAnalyzer"] = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Optional[str] = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: Optional[str] = None):
        if hasattr(self, "_initialized"):
            return
        if db_path is None:
            from .storage import TraceSqliteStorage
            db_path = TraceSqliteStorage().db_path
        self.db_path = db_path
        self._local = threading.local()
        self._initialized = True

    @classmethod
    def reset(cls):
        cls._instance = None

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def get_agent_stats(self, days: int = 7) -> Dict[str, Any]:
        """per-agent 统计：avg/p50/p90 延迟、成功率、avg tokens"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT
                 agent_attrs,
                 duration_ms,
                 status
               FROM spans
               WHERE span_type = 'agent'
                 AND start_time > datetime('now', ? || ' days')
            """,
            (f"-{days}",),
        ).fetchall()

        agent_data: Dict[str, List[float]] = {}
        agent_errors: Dict[str, int] = {}
        agent_tokens: Dict[str, List[int]] = {}

        for row in rows:
            attrs = json.loads(row["agent_attrs"]) if row["agent_attrs"] else {}
            agent_id = attrs.get("agent_id", "unknown")
            duration = row["duration_ms"] or 0
            tokens = attrs.get("total_tokens", 0)

            agent_data.setdefault(agent_id, []).append(duration)
            agent_tokens.setdefault(agent_id, []).append(tokens)
            if row["status"] == "error":
                agent_errors[agent_id] = agent_errors.get(agent_id, 0) + 1

        result = {}
        for agent_id, durations in agent_data.items():
            durations.sort()
            n = len(durations)
            result[agent_id] = {
                "call_count": n,
                "avg_duration_ms": round(sum(durations) / n, 1),
                "p50_ms": round(durations[n // 2], 1),
                "p90_ms": round(durations[int(n * 0.9)], 1) if n >= 10 else round(durations[-1], 1),
                "success_rate": round(1 - agent_errors.get(agent_id, 0) / n, 3),
                "avg_tokens": round(sum(agent_tokens[agent_id]) / n),
            }
        return result

    def get_tool_stats(self, days: int = 7) -> Dict[str, Any]:
        """per-tool 统计：avg 延迟、成功率、调用频率"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT
                 tool_attrs,
                 duration_ms,
                 status
               FROM spans
               WHERE span_type = 'tool'
                 AND start_time > datetime('now', ? || ' days')
            """,
            (f"-{days}",),
        ).fetchall()

        tool_data: Dict[str, List[float]] = {}
        tool_errors: Dict[str, int] = {}

        for row in rows:
            attrs = json.loads(row["tool_attrs"]) if row["tool_attrs"] else {}
            tool_name = attrs.get("tool_name", "unknown")
            duration = row["duration_ms"] or 0

            tool_data.setdefault(tool_name, []).append(duration)
            if row["status"] == "error":
                tool_errors[tool_name] = tool_errors.get(tool_name, 0) + 1

        result = {}
        for tool_name, durations in tool_data.items():
            n = len(durations)
            result[tool_name] = {
                "call_count": n,
                "avg_duration_ms": round(sum(durations) / n, 1),
                "success_rate": round(1 - tool_errors.get(tool_name, 0) / n, 3),
            }
        return result

    def get_llm_stats(self, days: int = 7) -> Dict[str, Any]:
        """LLM 调用统计"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT
                 llm_attrs,
                 duration_ms
               FROM spans
               WHERE span_type = 'llm'
                 AND start_time > datetime('now', ? || ' days')
            """,
            (f"-{days}",),
        ).fetchall()

        durations = []
        prompt_tokens_total = 0
        completion_tokens_total = 0
        call_count = 0

        for row in rows:
            attrs = json.loads(row["llm_attrs"]) if row["llm_attrs"] else {}
            durations.append(row["duration_ms"] or 0)
            prompt_tokens_total += attrs.get("prompt_tokens", 0)
            completion_tokens_total += attrs.get("completion_tokens", 0)
            call_count += 1

        if call_count == 0:
            return {"call_count": 0}

        durations.sort()
        n = len(durations)
        return {
            "call_count": n,
            "avg_latency_ms": round(sum(durations) / n, 1),
            "p50_ms": round(durations[n // 2], 1),
            "p90_ms": round(durations[int(n * 0.9)], 1) if n >= 10 else round(durations[-1], 1),
            "avg_prompt_tokens": round(prompt_tokens_total / n),
            "avg_completion_tokens": round(completion_tokens_total / n),
            "total_prompt_tokens": prompt_tokens_total,
            "total_completion_tokens": completion_tokens_total,
        }

    def get_stage_breakdown(self, trace_id: str) -> Dict[str, float]:
        """单个 trace 的阶段耗时分布"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT name, duration_ms FROM spans
               WHERE trace_id = ? AND span_type = 'stage'
               ORDER BY start_time""",
            (trace_id,),
        ).fetchall()
        return {row["name"]: round(row["duration_ms"] or 0, 1) for row in rows}

    def get_slow_traces(
        self, threshold_ms: float = 30000, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """查询慢 trace"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT trace_id, session_id, duration_ms, mode,
                      agents_involved, question_summary
               FROM traces
               WHERE duration_ms > ?
               ORDER BY duration_ms DESC
               LIMIT ?""",
            (threshold_ms, limit),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["agents_involved"] = json.loads(d["agents_involved"]) if d["agents_involved"] else []
            results.append(d)
        return results

    def get_error_traces(
        self, days: int = 7, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """查询错误 trace"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT trace_id, session_id, duration_ms, mode,
                      question_summary, start_time
               FROM traces
               WHERE status = 'error'
                 AND start_time > datetime('now', ? || ' days')
               ORDER BY start_time DESC
               LIMIT ?""",
            (f"-{days}", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_waterfall(self, trace_id: str) -> Dict[str, Any]:
        """获取 waterfall 视图数据"""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, parent_id, span_type, name, status,
                      start_time, end_time, duration_ms, error_message,
                      llm_attrs, tool_attrs, agent_attrs
               FROM spans
               WHERE trace_id = ?
               ORDER BY start_time""",
            (trace_id,),
        ).fetchall()

        if not rows:
            return {"trace_id": trace_id, "total_duration_ms": 0, "spans": []}

        # 获取 trace 根 span 的起始时间
        trace_start = None
        for row in rows:
            if row["span_type"] == "trace":
                from datetime import datetime as dt
                trace_start = dt.fromisoformat(row["start_time"])
                break

        # 计算 depth（通过 parent_id 链）
        span_map = {row["id"]: dict(row) for row in rows}
        depth_map: Dict[str, int] = {}

        def _calc_depth(span_id: str) -> int:
            if span_id in depth_map:
                return depth_map[span_id]
            span = span_map.get(span_id)
            if not span or not span["parent_id"]:
                depth_map[span_id] = 0
                return 0
            d = _calc_depth(span["parent_id"]) + 1
            depth_map[span_id] = d
            return d

        waterfall_spans = []
        for row in rows:
            from datetime import datetime as dt
            row_dict = dict(row)
            start = dt.fromisoformat(row_dict["start_time"])
            offset_ms = (start - trace_start).total_seconds() * 1000 if trace_start else 0

            attrs = {}
            if row_dict.get("llm_attrs"):
                attrs["llm"] = json.loads(row_dict["llm_attrs"])
            if row_dict.get("tool_attrs"):
                attrs["tool"] = json.loads(row_dict["tool_attrs"])
            if row_dict.get("agent_attrs"):
                attrs["agent"] = json.loads(row_dict["agent_attrs"])

            waterfall_spans.append({
                "id": row_dict["id"],
                "parent_id": row_dict["parent_id"],
                "span_type": row_dict["span_type"],
                "name": row_dict["name"],
                "status": row_dict["status"],
                "start_offset_ms": round(offset_ms, 1),
                "duration_ms": round(row_dict["duration_ms"] or 0, 1),
                "depth": _calc_depth(row_dict["id"]),
                "error_message": row_dict.get("error_message"),
                "attributes": attrs,
            })

        total_duration = max(
            (s["start_offset_ms"] + s["duration_ms"] for s in waterfall_spans), default=0
        )
        return {
            "trace_id": trace_id,
            "total_duration_ms": round(total_duration, 1),
            "spans": waterfall_spans,
        }
