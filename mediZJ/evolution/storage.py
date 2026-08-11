"""自进化数据的 SQLite 持久化。"""

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .config import EvolutionSettings
from .source_catalog import get_source_locations


_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "memory", "data", "sessions.db"
)

_SCHEMA_VERSION = 3

_EXPERIENCE_TYPES = {
    "response_strategy",
    "prompt_guidance",
    "routing_rule",
    "retrieval_hint",
    "context_strategy",
}


class RollbackBlockedError(ValueError):
    """发布快照包含当前不可恢复的经验。"""

    def __init__(self, blockers: List[Dict[str, str]]):
        super().__init__("发布版本包含不可恢复的经验")
        self.blockers = blockers


class EvolutionStorage:
    """反馈、评审任务、案例、经验和发布版本存储。"""

    _instance: Optional["EvolutionStorage"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
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
        self._execute(self._migrate_tables)
        self._initialized = True

    @classmethod
    def reset(cls) -> None:
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
    def _create_tables(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_feedback (
                feedback_id         TEXT PRIMARY KEY,
                assistant_message_id INTEGER NOT NULL,
                user_id             TEXT NOT NULL,
                rating              TEXT NOT NULL,
                reason_codes        TEXT NOT NULL DEFAULT '[]',
                comment             TEXT NOT NULL DEFAULT '',
                version             INTEGER NOT NULL DEFAULT 1,
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE(assistant_message_id, user_id),
                FOREIGN KEY (assistant_message_id)
                    REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS evaluation_jobs (
                job_id               TEXT PRIMARY KEY,
                assistant_message_id INTEGER NOT NULL,
                user_id              TEXT NOT NULL,
                trigger_type         TEXT NOT NULL,
                feedback_version     INTEGER NOT NULL DEFAULT 0,
                status               TEXT NOT NULL DEFAULT 'pending',
                attempts             INTEGER NOT NULL DEFAULT 0,
                scheduled_at         TEXT NOT NULL,
                lease_until          TEXT,
                last_error           TEXT,
                feedback_snapshot    TEXT,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                UNIQUE(assistant_message_id, feedback_version),
                FOREIGN KEY (assistant_message_id)
                    REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS conversation_evaluations (
                evaluation_id        TEXT PRIMARY KEY,
                job_id               TEXT NOT NULL UNIQUE,
                assistant_message_id INTEGER NOT NULL,
                user_id              TEXT NOT NULL,
                overall_score        REAL NOT NULL,
                dimension_scores     TEXT NOT NULL,
                verdict              TEXT NOT NULL,
                safety_violation     INTEGER NOT NULL DEFAULT 0,
                attribution          TEXT NOT NULL,
                rationale            TEXT NOT NULL DEFAULT '',
                recommendations      TEXT NOT NULL DEFAULT '[]',
                extracted_experience TEXT,
                judge_model          TEXT NOT NULL DEFAULT '',
                rubric_version       TEXT NOT NULL DEFAULT 'v1',
                is_superseded         INTEGER NOT NULL DEFAULT 0,
                created_at           TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES evaluation_jobs(job_id),
                FOREIGN KEY (assistant_message_id)
                    REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS failure_cases (
                failure_id      TEXT PRIMARY KEY,
                evaluation_id   TEXT NOT NULL UNIQUE,
                user_id         TEXT NOT NULL,
                root_causes     TEXT NOT NULL,
                evidence        TEXT NOT NULL DEFAULT '[]',
                recommended_fix TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'open',
                created_at      TEXT NOT NULL,
                FOREIGN KEY (evaluation_id)
                    REFERENCES conversation_evaluations(evaluation_id)
            );

            CREATE TABLE IF NOT EXISTS learned_experiences (
                experience_id   TEXT PRIMARY KEY,
                experience_type TEXT NOT NULL,
                scope           TEXT NOT NULL,
                owner_user_id   TEXT,
                query_pattern   TEXT NOT NULL,
                content         TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'candidate',
                average_score   REAL NOT NULL DEFAULT 0,
                support_count   INTEGER NOT NULL DEFAULT 1,
                conflict_count  INTEGER NOT NULL DEFAULT 0,
                version         INTEGER NOT NULL DEFAULT 1,
                supersedes_id   TEXT,
                applicability   TEXT NOT NULL DEFAULT '[]',
                exclusions      TEXT NOT NULL DEFAULT '[]',
                prerequisites   TEXT NOT NULL DEFAULT '[]',
                safety_notes    TEXT NOT NULL DEFAULT '',
                evidence_refs   TEXT NOT NULL DEFAULT '[]',
                risk_level      TEXT NOT NULL DEFAULT 'low',
                capability_tag  TEXT NOT NULL DEFAULT '',
                distinct_users  INTEGER NOT NULL DEFAULT 1,
                negative_count  INTEGER NOT NULL DEFAULT 0,
                expires_at      TEXT,
                last_validated_at TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS experience_sources (
                experience_id TEXT NOT NULL,
                evaluation_id TEXT NOT NULL,
                PRIMARY KEY (experience_id, evaluation_id),
                FOREIGN KEY (experience_id)
                    REFERENCES learned_experiences(experience_id) ON DELETE CASCADE,
                FOREIGN KEY (evaluation_id)
                    REFERENCES conversation_evaluations(evaluation_id)
            );

            CREATE TABLE IF NOT EXISTS experience_supports (
                experience_id       TEXT NOT NULL,
                assistant_message_id INTEGER NOT NULL,
                evaluation_id       TEXT NOT NULL,
                user_id             TEXT NOT NULL,
                score               REAL NOT NULL,
                created_at          TEXT NOT NULL,
                PRIMARY KEY (experience_id, assistant_message_id),
                FOREIGN KEY (experience_id)
                    REFERENCES learned_experiences(experience_id) ON DELETE CASCADE,
                FOREIGN KEY (evaluation_id)
                    REFERENCES conversation_evaluations(evaluation_id) ON DELETE CASCADE,
                FOREIGN KEY (assistant_message_id)
                    REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS experience_exposures (
                experience_id       TEXT NOT NULL,
                assistant_message_id INTEGER NOT NULL,
                user_id             TEXT NOT NULL,
                bucket              TEXT NOT NULL,
                applied             INTEGER NOT NULL,
                created_at          TEXT NOT NULL,
                PRIMARY KEY (experience_id, assistant_message_id),
                FOREIGN KEY (experience_id)
                    REFERENCES learned_experiences(experience_id) ON DELETE CASCADE,
                FOREIGN KEY (assistant_message_id)
                    REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS evolution_schema_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_releases (
                release_id       TEXT PRIMARY KEY,
                version          INTEGER NOT NULL UNIQUE,
                active_ids       TEXT NOT NULL,
                previous_version INTEGER,
                action           TEXT NOT NULL,
                operator_user_id TEXT NOT NULL,
                created_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_deletion_audits (
                audit_id              TEXT PRIMARY KEY,
                session_id_hash       TEXT NOT NULL UNIQUE,
                user_id_hash          TEXT NOT NULL,
                deleted_message_count INTEGER NOT NULL DEFAULT 0,
                deleted_feedback_count INTEGER NOT NULL DEFAULT 0,
                deleted_job_count     INTEGER NOT NULL DEFAULT 0,
                deleted_evaluation_count INTEGER NOT NULL DEFAULT 0,
                deleted_failure_count INTEGER NOT NULL DEFAULT 0,
                deleted_trace_count   INTEGER NOT NULL DEFAULT 0,
                affected_experience_ids TEXT NOT NULL DEFAULT '[]',
                demoted_experience_ids  TEXT NOT NULL DEFAULT '[]',
                cleanup_status        TEXT NOT NULL DEFAULT 'pending',
                cleanup_errors        TEXT NOT NULL DEFAULT '[]',
                created_at            TEXT NOT NULL,
                cleanup_completed_at  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_message
                ON conversation_feedback(assistant_message_id);
            CREATE INDEX IF NOT EXISTS idx_eval_jobs_status
                ON evaluation_jobs(status, scheduled_at);
            CREATE INDEX IF NOT EXISTS idx_evaluations_user
                ON conversation_evaluations(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_experiences_active
                ON learned_experiences(status, scope, owner_user_id);
            CREATE INDEX IF NOT EXISTS idx_deletion_audits_created
                ON session_deletion_audits(created_at);
            CREATE INDEX IF NOT EXISTS idx_exposure_bucket
                ON experience_exposures(experience_id, bucket, user_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_state
                ON evaluation_jobs(status, updated_at);
            """
        )

    @staticmethod
    def _migrate_tables(conn: sqlite3.Connection) -> None:
        """为已有经验库补充治理与适用边界字段。"""
        migrations = [
            ("applicability", "TEXT NOT NULL DEFAULT '[]'"),
            ("exclusions", "TEXT NOT NULL DEFAULT '[]'"),
            ("prerequisites", "TEXT NOT NULL DEFAULT '[]'"),
            ("safety_notes", "TEXT NOT NULL DEFAULT ''"),
            ("evidence_refs", "TEXT NOT NULL DEFAULT '[]'"),
            ("risk_level", "TEXT NOT NULL DEFAULT 'low'"),
            ("capability_tag", "TEXT NOT NULL DEFAULT ''"),
            ("distinct_users", "INTEGER NOT NULL DEFAULT 1"),
            ("negative_count", "INTEGER NOT NULL DEFAULT 0"),
            ("expires_at", "TEXT"),
            ("last_validated_at", "TEXT"),
        ]
        for column, definition in migrations:
            try:
                conn.execute(
                    f"ALTER TABLE learned_experiences "
                    f"ADD COLUMN {column} {definition}"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        job_migrations = [
            ("feedback_snapshot", "TEXT"),
        ]
        evaluation_migrations = [
            ("is_superseded", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for table, columns in (
            ("evaluation_jobs", job_migrations),
            ("conversation_evaluations", evaluation_migrations),
        ):
            for column, definition in columns:
                try:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        version = conn.execute(
            "SELECT value FROM evolution_schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version is None:
            EvolutionStorage._clear_legacy_evolution_data(conn)
            conn.execute(
                "INSERT INTO evolution_schema_meta VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
        elif int(version["value"]) < _SCHEMA_VERSION:
            EvolutionStorage._clear_legacy_evolution_data(conn)
            conn.execute(
                "UPDATE evolution_schema_meta SET value = ? "
                "WHERE key = 'schema_version'",
                (str(_SCHEMA_VERSION),),
            )
        conn.execute(
            """
            UPDATE learned_experiences
            SET distinct_users = MAX(
                distinct_users,
                COALESCE((
                    SELECT COUNT(DISTINCT ce.user_id)
                    FROM experience_sources AS es
                    JOIN conversation_evaluations AS ce
                      ON ce.evaluation_id = es.evaluation_id
                    WHERE es.experience_id = learned_experiences.experience_id
                ), 0)
            )
            """
        )
        settings = EvolutionSettings.from_env()
        conn.execute(
            """
            UPDATE learned_experiences
            SET status = 'candidate', updated_at = ?
            WHERE scope = 'global' AND status IN ('active', 'observing')
              AND (support_count < ? OR distinct_users < ?)
            """,
            (
                datetime.now().isoformat(),
                settings.global_min_support,
                settings.global_min_support,
            ),
        )

    @staticmethod
    def _clear_legacy_evolution_data(conn: sqlite3.Connection) -> None:
        """一次性清空旧自进化业务数据，保留会话、Trace 与删除审计。"""
        for table in (
            "experience_exposures",
            "experience_supports",
            "experience_sources",
            "failure_cases",
            "conversation_evaluations",
            "evaluation_jobs",
            "conversation_feedback",
            "strategy_releases",
            "learned_experiences",
        ):
            conn.execute(f"DELETE FROM {table}")

    def get_message_context(
        self,
        message_id: int,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """读取评审所需的回答、问题、会话及 Trace。"""

        def _do_get(conn: sqlite3.Connection):
            params: List[Any] = [message_id]
            user_clause = ""
            if user_id is not None:
                user_clause = " AND s.user_id = ?"
                params.append(user_id)
            row = conn.execute(
                """
                SELECT m.*, s.user_id, s.session_id
                FROM messages AS m
                JOIN sessions AS s ON s.session_id = m.session_id
                WHERE m.id = ? AND m.role = 'assistant'
                """ + user_clause,
                tuple(params),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            question = conn.execute(
                """
                SELECT content FROM messages
                WHERE session_id = ? AND turn_index = ? AND role = 'user'
                ORDER BY id LIMIT 1
                """,
                (result["session_id"], result["turn_index"]),
            ).fetchone()
            result["question"] = question["content"] if question else ""
            feedback = conn.execute(
                "SELECT * FROM conversation_feedback "
                "WHERE assistant_message_id = ? AND user_id = ?",
                (message_id, result["user_id"]),
            ).fetchone()
            result["feedback"] = dict(feedback) if feedback else None
            try:
                trace = conn.execute(
                    "SELECT tree_json FROM traces WHERE trace_id = ?",
                    (result.get("trace_id"),),
                ).fetchone()
            except sqlite3.OperationalError:
                trace = None
            result["trace"] = json.loads(trace["tree_json"]) if trace else {}
            for field in ("agent_events", "citations"):
                value = result.get(field)
                if isinstance(value, str):
                    try:
                        result[field] = json.loads(value)
                    except json.JSONDecodeError:
                        result[field] = []
            return result

        return self._execute(_do_get)

    def delete_session_data(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """事务化删除会话原始数据，并重算受影响的经验。"""

        def _do_delete(conn: sqlite3.Connection):
            conn.execute("BEGIN IMMEDIATE")
            params: List[Any] = [session_id]
            owner_clause = ""
            if user_id is not None:
                owner_clause = " AND user_id = ?"
                params.append(user_id)
            session = conn.execute(
                "SELECT user_id FROM sessions WHERE session_id = ?"
                + owner_clause,
                tuple(params),
            ).fetchone()
            if session is None:
                return None

            counts = self._session_deletion_counts(conn, session_id)
            affected_ids = [
                row["experience_id"]
                for row in conn.execute(
                    """
                    SELECT DISTINCT sources.experience_id
                    FROM experience_sources AS sources
                    JOIN conversation_evaluations AS evaluations
                      ON evaluations.evaluation_id = sources.evaluation_id
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = ?
                    """,
                    (session_id,),
                ).fetchall()
            ]
            negative_impacts, conflict_impacts = self._experience_impacts(
                conn,
                session_id,
            )

            conn.execute(
                """
                DELETE FROM experience_sources
                WHERE evaluation_id IN (
                    SELECT evaluations.evaluation_id
                    FROM conversation_evaluations AS evaluations
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = ?
                )
                """,
                (session_id,),
            )
            conn.execute(
                """
                DELETE FROM failure_cases
                WHERE evaluation_id IN (
                    SELECT evaluations.evaluation_id
                    FROM conversation_evaluations AS evaluations
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = ?
                )
                """,
                (session_id,),
            )
            conn.execute(
                """
                DELETE FROM conversation_evaluations
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            conn.execute(
                """
                DELETE FROM conversation_feedback
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            conn.execute(
                """
                DELETE FROM evaluation_jobs
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            try:
                conn.execute("DELETE FROM traces WHERE session_id = ?", (session_id,))
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

            demoted_ids = self._recalculate_experiences(
                conn,
                affected_ids,
                negative_impacts,
                conflict_impacts,
            )
            now = datetime.now().isoformat()
            session_hash = self._identifier_hash(session_id)
            conn.execute(
                """
                INSERT INTO session_deletion_audits
                    (audit_id, session_id_hash, user_id_hash,
                     deleted_message_count, deleted_feedback_count,
                     deleted_job_count, deleted_evaluation_count,
                     deleted_failure_count, deleted_trace_count,
                     affected_experience_ids, demoted_experience_ids,
                     cleanup_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    str(uuid.uuid4()),
                    session_hash,
                    self._identifier_hash(session["user_id"]),
                    counts["messages"],
                    counts["feedback"],
                    counts["jobs"],
                    counts["evaluations"],
                    counts["failures"],
                    counts["traces"],
                    json.dumps(affected_ids),
                    json.dumps(demoted_ids),
                    now,
                ),
            )
            return {
                "session_id_hash": session_hash,
                "affected_experience_ids": affected_ids,
                "demoted_experience_ids": demoted_ids,
            }

        return self._execute(_do_delete)

    def complete_session_cleanup(
        self,
        session_id_hash: str,
        errors: List[str],
    ) -> None:
        """记录数据库外部的向量和文件清理结果。"""

        def _do_complete(conn: sqlite3.Connection):
            conn.execute(
                """
                UPDATE session_deletion_audits
                SET cleanup_status = ?, cleanup_errors = ?,
                    cleanup_completed_at = ?
                WHERE session_id_hash = ?
                """,
                (
                    "completed" if not errors else "partial",
                    json.dumps(errors, ensure_ascii=False),
                    datetime.now().isoformat(),
                    session_id_hash,
                ),
            )

        self._execute(_do_complete)

    @staticmethod
    def _identifier_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _session_deletion_counts(
        conn: sqlite3.Connection,
        session_id: str,
    ) -> Dict[str, int]:
        queries = {
            "messages": "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            "feedback": """
                SELECT COUNT(*) FROM conversation_feedback
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = ?)
            """,
            "jobs": """
                SELECT COUNT(*) FROM evaluation_jobs
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = ?)
            """,
            "evaluations": """
                SELECT COUNT(*) FROM conversation_evaluations
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = ?)
            """,
            "failures": """
                SELECT COUNT(*) FROM failure_cases
                WHERE evaluation_id IN (
                    SELECT evaluations.evaluation_id
                    FROM conversation_evaluations AS evaluations
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = ?)
            """,
        }
        counts = {
            key: conn.execute(query, (session_id,)).fetchone()[0]
            for key, query in queries.items()
        }
        try:
            counts["traces"] = conn.execute(
                "SELECT COUNT(*) FROM traces WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            counts["traces"] = 0
        return counts

    def _experience_impacts(
        self,
        conn: sqlite3.Connection,
        session_id: str,
    ) -> tuple[Dict[str, int], Dict[str, int]]:
        """计算待删除负反馈和失败评审对已应用经验的影响。"""
        negative: Dict[str, int] = {}
        conflicts: Dict[str, int] = {}
        rows = conn.execute(
            """
            SELECT messages.id,
                   feedback.rating,
                   COALESCE(SUM(CASE
                       WHEN evaluations.verdict = 'low'
                         OR evaluations.safety_violation = 1
                       THEN 1 ELSE 0 END), 0) AS failures
            FROM messages
            LEFT JOIN conversation_feedback AS feedback
              ON feedback.assistant_message_id = messages.id
            LEFT JOIN conversation_evaluations AS evaluations
              ON evaluations.assistant_message_id = messages.id
            WHERE messages.session_id = ? AND messages.role = 'assistant'
            GROUP BY messages.id, feedback.rating
            """,
            (session_id,),
        ).fetchall()
        for row in rows:
            applied_ids = self._get_applied_experience_ids(conn, row["id"])
            for experience_id in applied_ids:
                if row["rating"] == "dislike":
                    negative[experience_id] = negative.get(experience_id, 0) + 1
                if row["failures"]:
                    conflicts[experience_id] = (
                        conflicts.get(experience_id, 0) + row["failures"]
                    )
        return negative, conflicts

    def _recalculate_experiences(
        self,
        conn: sqlite3.Connection,
        source_experience_ids: List[str],
        negative_impacts: Dict[str, int],
        conflict_impacts: Dict[str, int],
    ) -> List[str]:
        """根据剩余评审证据重算经验统计并执行降级。"""
        all_ids = set(source_experience_ids) | set(negative_impacts) | set(
            conflict_impacts
        )
        demoted_ids: List[str] = []
        release_required = False
        now = datetime.now().isoformat()
        for experience_id in all_ids:
            row = conn.execute(
                "SELECT * FROM learned_experiences WHERE experience_id = ?",
                (experience_id,),
            ).fetchone()
            if row is None:
                continue
            aggregate = conn.execute(
                """
                SELECT COUNT(*) AS support_count,
                       COALESCE(AVG(score), 0) AS average,
                       COUNT(DISTINCT user_id) AS distinct_users
                FROM experience_supports
                WHERE experience_id = ?
                """,
                (experience_id,),
            ).fetchone()
            status = row["status"]
            support_count = aggregate["support_count"]
            if support_count == 0 and status != "retired":
                status = "retired"
            negative_count = max(
                0,
                row["negative_count"] - negative_impacts.get(experience_id, 0),
            )
            conflict_count = max(
                0,
                row["conflict_count"] - conflict_impacts.get(experience_id, 0),
            )
            conn.execute(
                """
                UPDATE learned_experiences
                SET support_count = ?, average_score = ?, distinct_users = ?,
                    negative_count = ?, conflict_count = ?, status = ?,
                    updated_at = ?
                WHERE experience_id = ?
                """,
                (
                    support_count,
                    aggregate["average"],
                    aggregate["distinct_users"],
                    negative_count,
                    conflict_count,
                    status,
                    now,
                    experience_id,
                ),
            )
            refreshed = conn.execute(
                "SELECT * FROM learned_experiences WHERE experience_id = ?",
                (experience_id,),
            ).fetchone()
            if status in {"active", "observing"}:
                try:
                    self._validate_publication(refreshed)
                except ValueError:
                    status = "candidate"
                    conn.execute(
                        "UPDATE learned_experiences SET status = ? "
                        "WHERE experience_id = ?",
                        (status, experience_id),
                    )
            if row["status"] in {"active", "observing"} and status != row["status"]:
                demoted_ids.append(experience_id)
                release_required = True
        if release_required:
            self._create_release(conn, "auto_demote_session_deleted", "system")
        return demoted_ids

    def upsert_feedback(
        self,
        message_id: int,
        user_id: str,
        rating: str,
        reason_codes: List[str],
        comment: str,
    ) -> Dict[str, Any]:
        """新增或更新一条用户反馈。"""

        def _do_upsert(conn: sqlite3.Connection):
            owned = conn.execute(
                """
                SELECT 1 FROM messages AS m
                JOIN sessions AS s ON s.session_id = m.session_id
                WHERE m.id = ? AND m.role = 'assistant' AND s.user_id = ?
                """,
                (message_id, user_id),
            ).fetchone()
            if owned is None:
                raise LookupError("回答不存在")
            now = datetime.now().isoformat()
            existing = conn.execute(
                "SELECT * FROM conversation_feedback "
                "WHERE assistant_message_id = ? AND user_id = ?",
                (message_id, user_id),
            ).fetchone()
            if existing:
                previous_rating = existing["rating"]
                version = existing["version"] + 1
                conn.execute(
                    """
                    UPDATE conversation_feedback
                    SET rating = ?, reason_codes = ?, comment = ?,
                        version = ?, updated_at = ?
                    WHERE feedback_id = ?
                    """,
                    (
                        rating,
                        json.dumps(reason_codes, ensure_ascii=False),
                        comment,
                        version,
                        now,
                        existing["feedback_id"],
                    ),
                )
                feedback_id = existing["feedback_id"]
            else:
                previous_rating = None
                feedback_id = str(uuid.uuid4())
                version = 1
                conn.execute(
                    """
                    INSERT INTO conversation_feedback
                        (feedback_id, assistant_message_id, user_id, rating,
                         reason_codes, comment, version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        feedback_id,
                        message_id,
                        user_id,
                        rating,
                        json.dumps(reason_codes, ensure_ascii=False),
                        comment,
                        version,
                        now,
                        now,
                    ),
                )
            if previous_rating != rating:
                delta = (
                    1 if rating == "dislike" else -1
                    if previous_rating == "dislike" else 0
                )
                if delta:
                    applied_ids = self._get_applied_experience_ids(
                        conn,
                        message_id,
                    )
                    for experience_id in applied_ids:
                        conn.execute(
                            """
                            UPDATE learned_experiences
                            SET negative_count = MAX(0, negative_count + ?),
                                status = CASE
                                    WHEN ? > 0 AND status = 'observing'
                                    THEN 'retired'
                                    ELSE status
                                END,
                                updated_at = ?
                            WHERE experience_id = ?
                            """,
                            (delta, delta, now, experience_id),
                        )
                    if delta > 0 and applied_ids:
                        self._create_release(
                            conn,
                            "auto_retire_negative_feedback",
                            "system",
                        )
            conn.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'superseded', updated_at = ?
                WHERE assistant_message_id = ?
                  AND trigger_type = 'user_feedback'
                  AND feedback_version < ?
                  AND status = 'pending'
                """,
                (now, message_id, version),
            )
            return {
                "feedback_id": feedback_id,
                "assistant_message_id": str(message_id),
                "rating": rating,
                "reason_codes": reason_codes,
                "comment": comment,
                "version": version,
            }

        return self._execute(_do_upsert)

    @staticmethod
    def _get_applied_experience_ids(
        conn: sqlite3.Connection,
        message_id: int,
    ) -> List[str]:
        """从结构化曝光记录读取回答实际应用的经验。"""
        rows = conn.execute(
            """
            SELECT experience_id FROM experience_exposures
            WHERE assistant_message_id = ? AND applied = 1
            """,
            (message_id,),
        ).fetchall()
        return [row["experience_id"] for row in rows]

    def record_exposures(
        self,
        message_id: int,
        user_id: str,
        assignments: List[Dict[str, Any]],
    ) -> None:
        """持久化本次回答的经验实验分组。"""

        def _do_record(conn: sqlite3.Connection):
            now = datetime.now().isoformat()
            for assignment in assignments:
                bucket = assignment.get("bucket")
                if bucket not in {"active", "treatment", "control"}:
                    raise ValueError("非法经验实验分组")
                conn.execute(
                    """
                    INSERT INTO experience_exposures
                        (experience_id, assistant_message_id, user_id, bucket,
                         applied, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(experience_id, assistant_message_id) DO UPDATE SET
                        bucket = excluded.bucket,
                        applied = excluded.applied
                    """,
                    (
                        assignment["experience_id"],
                        message_id,
                        user_id,
                        bucket,
                        int(bool(assignment.get("applied"))),
                        now,
                    ),
                )

        self._execute(_do_record)

    def get_feedback(self, message_id: int, user_id: str) -> Optional[Dict[str, Any]]:
        def _do_get(conn: sqlite3.Connection):
            row = conn.execute(
                "SELECT * FROM conversation_feedback "
                "WHERE assistant_message_id = ? AND user_id = ?",
                (message_id, user_id),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["reason_codes"] = json.loads(result["reason_codes"])
            result["assistant_message_id"] = str(result["assistant_message_id"])
            return result

        return self._execute(_do_get)

    def enqueue_job(
        self,
        message_id: int,
        user_id: str,
        trigger_type: str,
        feedback_version: int = 0,
        feedback_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """幂等创建评审任务。"""

        def _do_enqueue(conn: sqlite3.Connection):
            now = datetime.now().isoformat()
            job_id = str(uuid.uuid4())
            cursor = conn.execute(
                """
                INSERT INTO evaluation_jobs
                    (job_id, assistant_message_id, user_id, trigger_type,
                     feedback_version, status, attempts, scheduled_at,
                     created_at, updated_at, feedback_snapshot)
                VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                ON CONFLICT(assistant_message_id, feedback_version) DO NOTHING
                """,
                (
                    job_id,
                    message_id,
                    user_id,
                    trigger_type,
                    feedback_version,
                    now,
                    now,
                    now,
                    json.dumps(feedback_snapshot, ensure_ascii=False)
                    if feedback_snapshot else None,
                ),
            )
            return job_id if cursor.rowcount else None

        return self._execute(_do_enqueue)

    def claim_job(self) -> Optional[Dict[str, Any]]:
        """领取一个待执行或租约过期的任务。"""

        def _do_claim(conn: sqlite3.Connection):
            now = datetime.now()
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM evaluation_jobs
                WHERE (status = 'pending' AND scheduled_at <= ?)
                   OR (status = 'running' AND lease_until < ?)
                ORDER BY created_at LIMIT 1
                """,
                (now.isoformat(), now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            lease = (now + timedelta(minutes=5)).isoformat()
            cursor = conn.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'running', attempts = attempts + 1,
                    lease_until = ?, updated_at = ?
                WHERE job_id = ? AND status IN ('pending', 'running')
                """,
                (lease, now.isoformat(), row["job_id"]),
            )
            return dict(row) if cursor.rowcount else None

        return self._execute(_do_claim)

    def complete_job(self, job_id: str) -> None:
        self._set_job_status(job_id, "completed", None)

    def fail_job(self, job_id: str, error: str, max_attempts: int = 3) -> None:
        def _do_fail(conn: sqlite3.Connection):
            row = conn.execute(
                "SELECT attempts FROM evaluation_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            status = "failed" if not row or row["attempts"] >= max_attempts else "pending"
            conn.execute(
                """
                UPDATE evaluation_jobs
                SET status = ?, last_error = ?, lease_until = NULL,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (status, error[:2000], datetime.now().isoformat(), job_id),
            )

        self._execute(_do_fail)

    def _set_job_status(self, job_id: str, status: str, error: Optional[str]) -> None:
        def _do_set(conn: sqlite3.Connection):
            conn.execute(
                """
                UPDATE evaluation_jobs
                SET status = ?, last_error = ?, lease_until = NULL,
                    updated_at = ? WHERE job_id = ?
                """,
                (status, error, datetime.now().isoformat(), job_id),
            )

        self._execute(_do_set)

    def save_evaluation(
        self,
        job: Dict[str, Any],
        result: Dict[str, Any],
        judge_model: str,
    ) -> str:
        """保存评分，并生成失败案例或经验候选。"""

        def _do_save(conn: sqlite3.Connection):
            existing_evaluation = conn.execute(
                "SELECT evaluation_id FROM conversation_evaluations "
                "WHERE job_id = ?",
                (job["job_id"],),
            ).fetchone()
            if existing_evaluation:
                conn.execute(
                    "UPDATE evaluation_jobs SET status = 'completed', "
                    "lease_until = NULL, updated_at = ? WHERE job_id = ?",
                    (datetime.now().isoformat(), job["job_id"]),
                )
                return existing_evaluation["evaluation_id"]
            evaluation_id = str(uuid.uuid4())
            now = datetime.now().isoformat()
            overall = float(result["overall_score"])
            safety = bool(result.get("safety_violation", False))
            verdict = result.get("verdict") or (
                "low" if overall < 65 or safety else "high" if overall >= 85 else "medium"
            )
            proposed_experiences = result.get("experiences") or (
                [result["experience"]] if result.get("experience") else []
            )
            experiences = [
                experience for experience in proposed_experiences
                if isinstance(experience, dict)
                and experience.get("type", "response_strategy")
                in _EXPERIENCE_TYPES
            ]
            is_superseded = False
            if (
                job.get("trigger_type") == "user_feedback"
                and int(job.get("feedback_version") or 0) > 0
            ):
                current_feedback = conn.execute(
                    "SELECT version FROM conversation_feedback "
                    "WHERE assistant_message_id = ? AND user_id = ?",
                    (job["assistant_message_id"], job["user_id"]),
                ).fetchone()
                is_superseded = (
                    current_feedback is None
                    or current_feedback["version"] != job["feedback_version"]
                )
            conn.execute(
                """
                INSERT INTO conversation_evaluations
                    (evaluation_id, job_id, assistant_message_id, user_id,
                     overall_score, dimension_scores, verdict,
                     safety_violation, attribution, rationale,
                     recommendations, extracted_experience, judge_model,
                     rubric_version, is_superseded, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'v3', ?, ?)
                """,
                (
                    evaluation_id,
                    job["job_id"],
                    job["assistant_message_id"],
                    job["user_id"],
                    overall,
                    json.dumps(result.get("dimension_scores", {}), ensure_ascii=False),
                    verdict,
                    int(safety),
                    json.dumps(result.get("attribution", []), ensure_ascii=False),
                    result.get("rationale", ""),
                    json.dumps(result.get("recommendations", []), ensure_ascii=False),
                    json.dumps(experiences, ensure_ascii=False),
                    judge_model,
                    int(is_superseded),
                    now,
                ),
            )
            if not is_superseded and (verdict == "low" or safety):
                conn.execute(
                    """
                    INSERT INTO failure_cases
                        (failure_id, evaluation_id, user_id, root_causes,
                         evidence, recommended_fix, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        evaluation_id,
                        job["user_id"],
                        json.dumps(result.get("attribution", ["other"]), ensure_ascii=False),
                        json.dumps(result.get("evidence", []), ensure_ascii=False),
                        "；".join(result.get("recommendations", [])),
                        now,
                    ),
                )
                applied_ids = self._get_applied_experience_ids(
                    conn,
                    int(job["assistant_message_id"]),
                )
                supported_ids = [
                    row["experience_id"]
                    for row in conn.execute(
                        "SELECT experience_id FROM experience_supports "
                        "WHERE assistant_message_id = ?",
                        (job["assistant_message_id"],),
                    ).fetchall()
                ]
                conn.execute(
                    "DELETE FROM experience_supports "
                    "WHERE assistant_message_id = ?",
                    (job["assistant_message_id"],),
                )
                for experience_id in applied_ids:
                    conn.execute(
                        """
                        UPDATE learned_experiences
                        SET conflict_count = conflict_count + 1,
                            status = CASE
                                WHEN status IN ('active', 'observing')
                                THEN 'retired'
                                ELSE status
                            END,
                            updated_at = ?
                        WHERE experience_id = ?
                        """,
                        (now, experience_id),
                    )
                if applied_ids:
                    self._create_release(
                        conn,
                        "auto_retire_failed_evaluation",
                        "system",
                    )
                for experience_id in supported_ids:
                    self._recompute_experience_statistics(
                        conn,
                        experience_id,
                        now,
                    )
            if not is_superseded and verdict == "high":
                for experience in experiences:
                    self._upsert_experience(
                        conn,
                        evaluation_id,
                        job["user_id"],
                        overall,
                        experience,
                        int(job["assistant_message_id"]),
                        now,
                    )
            conn.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'completed', lease_until = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (now, job["job_id"]),
            )
            return evaluation_id

        return self._execute(_do_save)

    def _upsert_experience(
        self,
        conn: sqlite3.Connection,
        evaluation_id: str,
        user_id: str,
        overall: float,
        experience: Dict[str, Any],
        assistant_message_id: int,
        now: str,
    ) -> None:
        """以原子经验为单位聚合支持证据。"""
        scope = experience.get("scope", "private")
        owner = user_id if scope == "private" else None
        experience_type = experience.get("type", "response_strategy")
        if experience_type not in _EXPERIENCE_TYPES:
            return
        query_pattern = experience.get("query_pattern", "")
        existing = conn.execute(
            """
            SELECT * FROM learned_experiences
            WHERE experience_type = ? AND scope = ?
              AND COALESCE(owner_user_id, '') = COALESCE(?, '')
              AND query_pattern = ?
              AND status IN ('candidate', 'observing', 'active')
            ORDER BY version DESC LIMIT 1
            """,
            (experience_type, scope, owner, query_pattern),
        ).fetchone()
        json_fields = {
            "applicability": experience.get("applicability", []),
            "exclusions": experience.get("exclusions", []),
            "prerequisites": experience.get("prerequisites", []),
            "evidence_refs": experience.get("evidence_refs", []),
        }
        if existing:
            experience_id = existing["experience_id"]
            conn.execute(
                """
                UPDATE learned_experiences
                SET content = ?,
                    applicability = ?, exclusions = ?, prerequisites = ?,
                    safety_notes = ?, evidence_refs = ?, risk_level = ?,
                    capability_tag = ?, expires_at = ?,
                    last_validated_at = ?, updated_at = ?
                WHERE experience_id = ?
                """,
                (
                    experience.get("content", existing["content"]),
                    json.dumps(json_fields["applicability"], ensure_ascii=False),
                    json.dumps(json_fields["exclusions"], ensure_ascii=False),
                    json.dumps(json_fields["prerequisites"], ensure_ascii=False),
                    experience.get("safety_notes", ""),
                    json.dumps(json_fields["evidence_refs"], ensure_ascii=False),
                    experience.get("risk_level", "low"),
                    experience.get("capability_tag", ""),
                    experience.get("expires_at"),
                    now,
                    now,
                    experience_id,
                ),
            )
        else:
            experience_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO learned_experiences
                    (experience_id, experience_type, scope, owner_user_id,
                     query_pattern, content, status, average_score,
                     support_count, conflict_count, version, applicability,
                     exclusions, prerequisites, safety_notes, evidence_refs,
                     risk_level, capability_tag, distinct_users,
                     negative_count, expires_at, last_validated_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'candidate', 0, 0, 0, 1, ?, ?, ?,
                        ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                """,
                (
                    experience_id,
                    experience_type,
                    scope,
                    owner,
                    query_pattern,
                    experience.get("content", ""),
                    json.dumps(json_fields["applicability"], ensure_ascii=False),
                    json.dumps(json_fields["exclusions"], ensure_ascii=False),
                    json.dumps(json_fields["prerequisites"], ensure_ascii=False),
                    experience.get("safety_notes", ""),
                    json.dumps(json_fields["evidence_refs"], ensure_ascii=False),
                    experience.get("risk_level", "low"),
                    experience.get("capability_tag", ""),
                    experience.get("expires_at"),
                    now,
                    now,
                    now,
                ),
            )
        conn.execute(
            "INSERT OR IGNORE INTO experience_sources VALUES (?, ?)",
            (experience_id, evaluation_id),
        )
        conn.execute(
            """
            INSERT INTO experience_supports
                (experience_id, assistant_message_id, evaluation_id, user_id,
                 score, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(experience_id, assistant_message_id) DO UPDATE SET
                evaluation_id = excluded.evaluation_id,
                score = excluded.score,
                created_at = excluded.created_at
            """,
            (
                experience_id,
                assistant_message_id,
                evaluation_id,
                user_id,
                overall,
                now,
            ),
        )
        self._recompute_experience_statistics(conn, experience_id, now)
        row = conn.execute(
            "SELECT * FROM learned_experiences WHERE experience_id = ?",
            (experience_id,),
        ).fetchone()
        status = row["status"]
        if (
            scope == "private"
            and row["support_count"] >= 2
            and row["average_score"] >= 85
            and row["conflict_count"] == 0
            and row["negative_count"] == 0
            and row["risk_level"] != "high"
            and row["experience_type"] != "medical_knowledge"
        ):
            try:
                self._validate_publication(row)
            except ValueError:
                status = row["status"]
            else:
                status = "active"
        conn.execute(
            """
            UPDATE learned_experiences
            SET status = ?, updated_at = ?
            WHERE experience_id = ?
            """,
            (status, now, experience_id),
        )
        if status == "active" and row["status"] != "active":
            self._create_release(conn, "auto_promote", "system")

    @staticmethod
    def _recompute_experience_statistics(
        conn: sqlite3.Connection,
        experience_id: str,
        now: str,
    ) -> None:
        aggregate = conn.execute(
            """
            SELECT COUNT(*) AS support_count,
                   COALESCE(AVG(score), 0) AS average_score,
                   COUNT(DISTINCT user_id) AS distinct_users
            FROM experience_supports
            WHERE experience_id = ?
            """,
            (experience_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE learned_experiences
            SET support_count = ?, average_score = ?, distinct_users = ?,
                updated_at = ?
            WHERE experience_id = ?
            """,
            (
                aggregate["support_count"],
                aggregate["average_score"],
                aggregate["distinct_users"],
                now,
                experience_id,
            ),
        )

    def list_evaluations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """返回可追溯到原对话、反馈和 Trace 的评审列表。"""

        def _do_list(conn: sqlite3.Connection):
            rows = conn.execute(
                """
                SELECT ce.*,
                       answer.session_id,
                       answer.turn_index,
                       answer.content AS answer,
                       answer.trace_id,
                       question.content AS question,
                       sessions.created_at AS session_created_at,
                       users.username,
                       jobs.trigger_type,
                       feedback.rating AS feedback_rating,
                       feedback.reason_codes AS feedback_reason_codes,
                       feedback.comment AS feedback_comment
                FROM conversation_evaluations AS ce
                JOIN messages AS answer
                  ON answer.id = ce.assistant_message_id
                JOIN sessions
                  ON sessions.session_id = answer.session_id
                LEFT JOIN messages AS question
                  ON question.session_id = answer.session_id
                 AND question.turn_index = answer.turn_index
                 AND question.role = 'user'
                LEFT JOIN users
                  ON users.user_id = ce.user_id
                LEFT JOIN evaluation_jobs AS jobs
                  ON jobs.job_id = ce.job_id
                LEFT JOIN conversation_feedback AS feedback
                  ON feedback.assistant_message_id = ce.assistant_message_id
                 AND feedback.user_id = ce.user_id
                ORDER BY ce.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                for field in (
                    "dimension_scores",
                    "attribution",
                    "recommendations",
                    "feedback_reason_codes",
                ):
                    value = item.get(field)
                    if isinstance(value, str):
                        try:
                            item[field] = json.loads(value)
                        except json.JSONDecodeError:
                            item[field] = []
                items.append(item)
            return items

        return self._execute(_do_list)

    def list_failures(self, limit: int = 100) -> List[Dict[str, Any]]:
        """返回关联对话、Trace 和源码位置的失败案例。"""

        def _do_list(conn: sqlite3.Connection):
            rows = conn.execute(
                """
                SELECT failures.*,
                       evaluations.overall_score,
                       evaluations.rationale,
                       evaluations.assistant_message_id,
                       answer.session_id,
                       answer.turn_index,
                       answer.content AS answer,
                       answer.trace_id,
                       question.content AS question,
                       users.username,
                       jobs.trigger_type
                FROM failure_cases AS failures
                JOIN conversation_evaluations AS evaluations
                  ON evaluations.evaluation_id = failures.evaluation_id
                JOIN messages AS answer
                  ON answer.id = evaluations.assistant_message_id
                LEFT JOIN messages AS question
                  ON question.session_id = answer.session_id
                 AND question.turn_index = answer.turn_index
                 AND question.role = 'user'
                LEFT JOIN users
                  ON users.user_id = failures.user_id
                LEFT JOIN evaluation_jobs AS jobs
                  ON jobs.job_id = evaluations.job_id
                ORDER BY failures.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                for field in ("root_causes", "evidence"):
                    value = item.get(field)
                    if isinstance(value, str):
                        try:
                            item[field] = json.loads(value)
                        except json.JSONDecodeError:
                            item[field] = []
                item["source_locations"] = get_source_locations(
                    item["root_causes"]
                )
                items.append(item)
            return items

        return self._execute(_do_list)

    def list_experiences(
        self,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        def _do_list(conn: sqlite3.Connection):
            if status:
                rows = conn.execute(
                    "SELECT * FROM learned_experiences WHERE status = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM learned_experiences "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                for field in (
                    "applicability",
                    "exclusions",
                    "prerequisites",
                    "evidence_refs",
                ):
                    item[field] = json.loads(item.get(field) or "[]")
                metrics = self._observation_metrics(conn, item["experience_id"])
                item["observation_metrics"] = metrics
                try:
                    self._validate_observation(row)
                    item["eligible_for_observation"] = True
                    item["observation_blocker"] = ""
                except ValueError as exc:
                    item["eligible_for_observation"] = False
                    item["observation_blocker"] = str(exc)
                try:
                    self._validate_activation(row, metrics)
                    item["eligible_for_activation"] = True
                    item["activation_blocker"] = ""
                except ValueError as exc:
                    item["eligible_for_activation"] = False
                    item["activation_blocker"] = str(exc)
                uses_observation_gate = (
                    item["status"] == "candidate"
                    and item["scope"] == "global"
                )
                item["publishable"] = (
                    item["eligible_for_observation"]
                    if uses_observation_gate
                    else item["eligible_for_activation"]
                )
                item["publication_blocker"] = (
                    item["observation_blocker"]
                    if uses_observation_gate
                    else item["activation_blocker"]
                )
                items.append(item)
            return items

        return self._execute(_do_list)

    def apply_experience_action(
        self,
        experience_id: str,
        action: str,
        operator_user_id: str,
    ) -> bool:
        """执行观察、发布、驳回、重新应用、退役或删除动作。"""

        def _do_set(conn: sqlite3.Connection):
            row = conn.execute(
                "SELECT * FROM learned_experiences WHERE experience_id = ?",
                (experience_id,),
            ).fetchone()
            if row is None:
                return False
            if action == "delete":
                if row["status"] != "rejected":
                    raise ValueError("仅已驳回的经验可以删除")
                cursor = conn.execute(
                    "DELETE FROM learned_experiences WHERE experience_id = ?",
                    (experience_id,),
                )
                return bool(cursor.rowcount)
            transitions = {
                "observe": "observing",
                "activate": "active",
                "reject": "rejected",
                "retire": "retired",
                "reapply": "candidate",
            }
            if action not in transitions:
                raise ValueError("非法经验治理动作")
            target_status = transitions[action]
            if action == "observe":
                if row["scope"] != "global" or row["status"] != "candidate":
                    raise ValueError("仅全局候选经验可以进入观察")
                self._validate_observation(row)
            elif action == "activate":
                if row["scope"] == "global":
                    if row["status"] != "observing":
                        raise ValueError("全局经验必须先完成观察")
                    metrics = self._observation_metrics(conn, experience_id)
                    self._validate_activation(row, metrics)
                else:
                    self._validate_publication(row)
            elif action == "reject" and row["status"] not in {
                "candidate",
                "retired",
            }:
                raise ValueError("仅待审核或已停用的经验可以驳回")
            elif action == "reapply" and row["status"] != "rejected":
                raise ValueError("仅已驳回的经验可以重新应用")
            cursor = conn.execute(
                "UPDATE learned_experiences SET status = ?, updated_at = ? "
                "WHERE experience_id = ?",
                (target_status, datetime.now().isoformat(), experience_id),
            )
            if not cursor.rowcount:
                return False
            if target_status in {"active", "observing", "retired"}:
                self._create_release(conn, action, operator_user_id)
            return True

        return self._execute(_do_set)

    def set_experience_status(
        self,
        experience_id: str,
        status: str,
        operator_user_id: str,
    ) -> bool:
        """兼容内部调用，并映射为明确治理动作。"""

        row = self._execute(
            lambda conn: conn.execute(
                "SELECT scope, status FROM learned_experiences "
                "WHERE experience_id = ?",
                (experience_id,),
            ).fetchone()
        )
        if row is None:
            return False
        if status == "active" and row["scope"] == "global":
            action = "observe" if row["status"] == "candidate" else "activate"
        else:
            action = {
                "active": "activate",
                "rejected": "reject",
                "retired": "retire",
            }.get(status, status)
        return self.apply_experience_action(
            experience_id,
            action,
            operator_user_id,
        )

    @staticmethod
    def _observation_metrics(
        conn: sqlite3.Connection,
        experience_id: str,
    ) -> Dict[str, Any]:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT assistant_message_id, MAX(created_at) AS created_at
                FROM conversation_evaluations
                WHERE is_superseded = 0
                GROUP BY assistant_message_id
            )
            SELECT exposures.bucket,
                   COUNT(*) AS exposure_count,
                   COUNT(DISTINCT exposures.user_id) AS distinct_users,
                   COUNT(evaluations.evaluation_id) AS evaluated_count,
                   COALESCE(AVG(evaluations.overall_score), 0) AS average_score,
                   COALESCE(SUM(CASE WHEN evaluations.verdict = 'high'
                                     THEN 1 ELSE 0 END), 0) AS high_score_count,
                   COALESCE(SUM(CASE WHEN feedback.rating = 'dislike'
                                     THEN 1 ELSE 0 END), 0) AS negative_count,
                   COALESCE(SUM(CASE WHEN evaluations.safety_violation = 1
                                     THEN 1 ELSE 0 END), 0) AS safety_count
            FROM experience_exposures AS exposures
            LEFT JOIN latest
              ON latest.assistant_message_id = exposures.assistant_message_id
            LEFT JOIN conversation_evaluations AS evaluations
              ON evaluations.assistant_message_id = latest.assistant_message_id
             AND evaluations.created_at = latest.created_at
            LEFT JOIN conversation_feedback AS feedback
              ON feedback.assistant_message_id = exposures.assistant_message_id
             AND feedback.user_id = exposures.user_id
            WHERE exposures.experience_id = ?
              AND exposures.bucket IN ('treatment', 'control')
            GROUP BY exposures.bucket
            """,
            (experience_id,),
        ).fetchall()
        metrics = {
            "treatment": {
                "exposure_count": 0,
                "distinct_users": 0,
                "average_score": 0,
                "evaluated_count": 0,
                "high_score_count": 0,
                "negative_count": 0,
                "safety_count": 0,
            },
            "control": {
                "exposure_count": 0,
                "distinct_users": 0,
                "average_score": 0,
                "evaluated_count": 0,
                "high_score_count": 0,
                "negative_count": 0,
                "safety_count": 0,
            },
        }
        for row in rows:
            metrics[row["bucket"]] = {
                "exposure_count": row["exposure_count"],
                "distinct_users": row["distinct_users"],
                "average_score": round(row["average_score"] or 0, 2),
                "evaluated_count": row["evaluated_count"],
                "high_score_count": row["high_score_count"],
                "negative_count": row["negative_count"],
                "safety_count": row["safety_count"],
            }
        return metrics

    @staticmethod
    def _validate_observation(row: sqlite3.Row) -> None:
        EvolutionStorage._validate_publication(row)
        if row["scope"] != "global":
            raise ValueError("仅全局经验需要观察")

    @staticmethod
    def _validate_activation(
        row: sqlite3.Row,
        metrics: Dict[str, Any],
    ) -> None:
        EvolutionStorage._validate_publication(row)
        if row["scope"] != "global":
            return
        treatment = metrics["treatment"]
        control = metrics["control"]
        if treatment["distinct_users"] < 5 or control["distinct_users"] < 5:
            raise ValueError("观察组和对照组均至少需要 5 个不同用户")
        if treatment["evaluated_count"] < 5 or control["evaluated_count"] < 5:
            raise ValueError("观察组和对照组均至少需要 5 条有效评审")
        if treatment["high_score_count"] < 5:
            raise ValueError("观察经验至少需要 5 条有效高分支持")
        if treatment["average_score"] < 88:
            raise ValueError("观察组平均得分不得低于 88")
        if treatment["negative_count"] or treatment["safety_count"]:
            raise ValueError("观察组存在负反馈或安全违规")
        if treatment["average_score"] + 3 < control["average_score"]:
            raise ValueError("观察组得分显著低于对照组")

    @staticmethod
    def _validate_publication(row: sqlite3.Row) -> None:
        """强制校验经验的发布证据与医疗安全边界。"""
        if row["conflict_count"] > 0:
            raise ValueError("存在冲突案例，不能发布")
        if row["negative_count"] > 0:
            raise ValueError("存在负面反馈，不能发布")
        if row["scope"] == "global" and EvolutionStorage._row_has_personal_data(row):
            raise ValueError("全局经验包含个人身份信息")
        evidence_refs = json.loads(row["evidence_refs"] or "[]")
        prerequisites = json.loads(row["prerequisites"] or "[]")
        settings = EvolutionSettings.from_env()
        trusted_evidence = [
            evidence for evidence in evidence_refs
            if EvolutionStorage._is_trusted_evidence(evidence, settings)
        ]
        if row["experience_type"] == "medical_knowledge" and not trusted_evidence:
            raise ValueError("医学知识经验必须关联可核验的权威来源")
        if row["risk_level"] == "high" and (
            not trusted_evidence or not prerequisites or not row["safety_notes"]
        ):
            raise ValueError("高风险经验必须包含来源、前置条件和安全警示")
        if row["experience_type"] == "medical_knowledge" or row["risk_level"] == "high":
            if not row["expires_at"]:
                raise ValueError("医学知识和高风险经验必须设置有效期")
        if row["expires_at"]:
            try:
                expires_at = datetime.fromisoformat(row["expires_at"])
            except ValueError as exc:
                raise ValueError("经验有效期格式错误") from exc
            if expires_at <= datetime.now():
                raise ValueError("经验已经过期，必须重新认证")
        if row["scope"] == "private":
            if row["support_count"] < 2 or row["average_score"] < 85:
                raise ValueError("个人经验至少需 2 个支持案例且均分不低于 85")
            return
        if (
            row["support_count"] < settings.global_min_support
            or row["distinct_users"] < settings.global_min_support
        ):
            raise ValueError(
                "全局经验至少需 %d 个不同用户的支持案例"
                % settings.global_min_support
            )
        if row["average_score"] < 88:
            raise ValueError("全局经验平均得分不得低于 88")

    @staticmethod
    def _is_trusted_evidence(
        evidence: Any,
        settings: EvolutionSettings,
    ) -> bool:
        if not isinstance(evidence, dict):
            return False
        source = str(evidence.get("source", "")).strip()
        content = str(evidence.get("content", "")).strip()
        doc_id = str(evidence.get("doc_id", "")).strip()
        if source in settings.trusted_sources and doc_id and content:
            return True
        url = str(evidence.get("url", "")).strip()
        if not url or not content:
            return False
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and hostname in settings.trusted_domains
        )

    @staticmethod
    def _row_has_personal_data(row: sqlite3.Row) -> bool:
        fields = (
            "query_pattern",
            "content",
            "applicability",
            "exclusions",
            "prerequisites",
            "safety_notes",
            "evidence_refs",
            "capability_tag",
        )
        text = "\n".join(str(row[field] or "") for field in fields)
        patterns = (
            r"1[3-9]\d{9}",
            r"\b\d{17}[\dXx]\b",
            r"(?:姓名|称呼)\s*[：:]\s*[^，。\n]+",
        )
        return any(re.search(pattern, text) for pattern in patterns)

    @staticmethod
    def _create_release(
        conn: sqlite3.Connection,
        action: str,
        operator_user_id: str,
    ) -> None:
        row = conn.execute(
            "SELECT MAX(version) AS version FROM strategy_releases"
        ).fetchone()
        previous = row["version"] if row and row["version"] else None
        version = (previous or 0) + 1
        active = conn.execute(
            "SELECT experience_id, status FROM learned_experiences "
            "WHERE status IN ('active', 'observing') ORDER BY experience_id"
        ).fetchall()
        conn.execute(
            """
            INSERT INTO strategy_releases
                (release_id, version, active_ids, previous_version,
                 action, operator_user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                version,
                json.dumps(
                    {row["experience_id"]: row["status"] for row in active}
                ),
                previous,
                action,
                operator_user_id,
                datetime.now().isoformat(),
            ),
        )

    def list_releases(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _do_list(conn: sqlite3.Connection):
            rows = conn.execute(
                "SELECT * FROM strategy_releases "
                "ORDER BY version DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

        return self._execute(_do_list)

    def list_jobs(
        self,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按状态返回评审任务。"""

        def _do_list(conn: sqlite3.Connection):
            if status:
                rows = conn.execute(
                    "SELECT * FROM evaluation_jobs WHERE status = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM evaluation_jobs "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

        return self._execute(_do_list)

    def retry_job(self, job_id: str) -> bool:
        """将失败任务重新加入队列。"""

        def _do_retry(conn: sqlite3.Connection):
            now = datetime.now().isoformat()
            cursor = conn.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'pending', attempts = 0, scheduled_at = ?,
                    lease_until = NULL, last_error = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'failed'
                """,
                (now, now, job_id),
            )
            return bool(cursor.rowcount)

        return self._execute(_do_retry)

    def rollback_release(self, version: int, operator_user_id: str) -> bool:
        def _do_rollback(conn: sqlite3.Connection):
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT active_ids FROM strategy_releases WHERE version = ?",
                (version,),
            ).fetchone()
            if row is None:
                return False
            active_snapshot = json.loads(row["active_ids"])
            if isinstance(active_snapshot, list):
                active_snapshot = {
                    experience_id: "active"
                    for experience_id in active_snapshot
                }
            blockers = []
            for experience_id, status in active_snapshot.items():
                experience = conn.execute(
                    "SELECT * FROM learned_experiences WHERE experience_id = ?",
                    (experience_id,),
                ).fetchone()
                if experience is None:
                    blockers.append(
                        {
                            "experience_id": experience_id,
                            "blocker": "经验已不存在",
                        }
                    )
                    continue
                try:
                    if status == "active" and experience["scope"] == "global":
                        metrics = self._observation_metrics(conn, experience_id)
                        self._validate_activation(experience, metrics)
                    else:
                        self._validate_publication(experience)
                except ValueError as exc:
                    blockers.append(
                        {
                            "experience_id": experience_id,
                            "blocker": str(exc),
                        }
                    )
            if blockers:
                raise RollbackBlockedError(blockers)
            conn.execute(
                "UPDATE learned_experiences SET status = 'retired' "
                "WHERE status IN ('active', 'observing')"
            )
            for experience_id, status in active_snapshot.items():
                conn.execute(
                    "UPDATE learned_experiences SET status = ? "
                    "WHERE experience_id = ?",
                    (status, experience_id),
                )
            self._create_release(conn, f"rollback:{version}", operator_user_id)
            return True

        return self._execute(_do_rollback)

    def get_active_experiences(
        self,
        user_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """返回当前用户可用的私有和全局经验。"""

        def _do_get(conn: sqlite3.Connection):
            now = datetime.now().isoformat()
            cursor = conn.execute(
                """
                UPDATE learned_experiences
                SET status = 'candidate', updated_at = ?
                WHERE status IN ('active', 'observing')
                  AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (now, now),
            )
            if cursor.rowcount:
                self._create_release(conn, "auto_expire", "system")
            sql = """
                SELECT * FROM learned_experiences
                WHERE status IN ('active', 'observing')
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND (scope = 'global' OR owner_user_id = ?)
                ORDER BY CASE WHEN owner_user_id = ? THEN 0 ELSE 1 END,
                         average_score DESC
            """
            params: List[Any] = [now, user_id, user_id]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(row) for row in rows]

        return self._execute(_do_get)

    def overview(self) -> Dict[str, Any]:
        def _do_overview(conn: sqlite3.Connection):
            eval_row = conn.execute(
                "SELECT COUNT(*) AS count, AVG(overall_score) AS average "
                "FROM conversation_evaluations"
            ).fetchone()
            job_rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM evaluation_jobs "
                "GROUP BY status"
            ).fetchall()
            job_counts = {row["status"]: row["count"] for row in job_rows}
            exposure_rows = conn.execute(
                """
                SELECT bucket, COUNT(*) AS count,
                       COUNT(DISTINCT user_id) AS distinct_users
                FROM experience_exposures
                GROUP BY bucket
                """
            ).fetchall()
            exposure_counts = {
                row["bucket"]: {
                    "count": row["count"],
                    "distinct_users": row["distinct_users"],
                }
                for row in exposure_rows
            }
            return {
                "evaluation_count": eval_row["count"],
                "average_score": round(eval_row["average"] or 0, 2),
                "failure_count": conn.execute(
                    "SELECT COUNT(*) AS count FROM failure_cases"
                ).fetchone()["count"],
                "candidate_count": conn.execute(
                    "SELECT COUNT(*) AS count FROM learned_experiences "
                    "WHERE status = 'candidate'"
                ).fetchone()["count"],
                "active_count": conn.execute(
                    "SELECT COUNT(*) AS count FROM learned_experiences "
                    "WHERE status = 'active'"
                ).fetchone()["count"],
                "observing_count": conn.execute(
                    "SELECT COUNT(*) AS count FROM learned_experiences "
                    "WHERE status = 'observing'"
                ).fetchone()["count"],
                "job_counts": {
                    status: job_counts.get(status, 0)
                    for status in (
                        "pending",
                        "running",
                        "failed",
                        "superseded",
                        "completed",
                    )
                },
                "exposure_counts": exposure_counts,
            }

        return self._execute(_do_overview)
