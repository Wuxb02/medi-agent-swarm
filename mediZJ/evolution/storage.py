"""自进化数据的 MySQL 持久化。"""

from mediZJ.infrastructure.database import Connection, execute, transaction

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .config import EvolutionSettings
from .source_catalog import get_source_locations


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

    def __init__(self, blockers: list[dict[str, str]]):
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

    def __init__(self) -> None:
        """存储实例不在构造阶段访问数据库。"""
        self._initialized = True

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    async def _execute(self, func, *args, **kwargs):
        return await execute(func, *args, **kwargs)

    async def get_message_context(
        self,
        message_id: int,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """读取评审所需的回答、问题、会话及 Trace。"""

        async def _do_get(conn: Connection):
            params: List[Any] = [message_id]
            user_clause = ""
            if user_id is not None:
                user_clause = " AND s.user_id = %s"
                params.append(user_id)
            row = (
                await conn.execute(
                    """
                SELECT m.*, s.user_id, s.session_id
                FROM messages AS m
                JOIN sessions AS s ON s.session_id = m.session_id
                WHERE m.id = %s AND m.role = 'assistant'
                """
                    + user_clause,
                    tuple(params),
                )
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            question = (
                await conn.execute(
                    """
                SELECT content FROM messages
                WHERE session_id = %s AND turn_index = %s AND role = 'user'
                ORDER BY id LIMIT 1
                """,
                    (result["session_id"], result["turn_index"]),
                )
            ).fetchone()
            result["question"] = question["content"] if question else ""
            feedback = (
                await conn.execute(
                    "SELECT * FROM conversation_feedback WHERE assistant_message_id = %s AND user_id = %s",
                    (message_id, result["user_id"]),
                )
            ).fetchone()
            result["feedback"] = dict(feedback) if feedback else None
            trace = (
                await conn.execute(
                    "SELECT tree_json FROM traces WHERE trace_id = %s",
                    (result.get("trace_id"),),
                )
            ).fetchone()
            result["trace"] = json.loads(trace["tree_json"]) if trace else {}
            for field in ("agent_events", "citations"):
                value = result.get(field)
                if isinstance(value, str):
                    try:
                        result[field] = json.loads(value)
                    except json.JSONDecodeError:
                        result[field] = []
            return result

        return await self._execute(_do_get)

    async def delete_session_data(
        self,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """事务化删除会话原始数据，并重算受影响的经验。"""

        async def _do_delete(conn: Connection):
            params: List[Any] = [session_id]
            owner_clause = ""
            if user_id is not None:
                owner_clause = " AND user_id = %s"
                params.append(user_id)
            session = (
                await conn.execute(
                    "SELECT user_id FROM sessions WHERE session_id = %s" + owner_clause,
                    tuple(params),
                )
            ).fetchone()
            if session is None:
                return None

            counts = await self._session_deletion_counts(conn, session_id)
            affected_ids = [
                row["experience_id"]
                for row in (
                    await conn.execute(
                        """
                    SELECT DISTINCT sources.experience_id
                    FROM experience_sources AS sources
                    JOIN conversation_evaluations AS evaluations
                      ON evaluations.evaluation_id = sources.evaluation_id
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = %s
                    """,
                        (session_id,),
                    )
                ).fetchall()
            ]
            negative_impacts, conflict_impacts = await self._experience_impacts(
                conn,
                session_id,
            )

            (
                await conn.execute(
                    """
                DELETE FROM experience_sources
                WHERE evaluation_id IN (
                    SELECT evaluations.evaluation_id
                    FROM conversation_evaluations AS evaluations
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = %s
                )
                """,
                    (session_id,),
                )
            )
            (
                await conn.execute(
                    """
                DELETE FROM failure_cases
                WHERE evaluation_id IN (
                    SELECT evaluations.evaluation_id
                    FROM conversation_evaluations AS evaluations
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = %s
                )
                """,
                    (session_id,),
                )
            )
            (
                await conn.execute(
                    """
                DELETE FROM conversation_evaluations
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = %s
                )
                """,
                    (session_id,),
                )
            )
            (
                await conn.execute(
                    """
                DELETE FROM conversation_feedback
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = %s
                )
                """,
                    (session_id,),
                )
            )
            (
                await conn.execute(
                    """
                DELETE FROM evaluation_jobs
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = %s
                )
                """,
                    (session_id,),
                )
            )
            (
                await conn.execute(
                    "DELETE FROM traces WHERE session_id = %s", (session_id,)
                )
            )
            (
                await conn.execute(
                    "DELETE FROM messages WHERE session_id = %s", (session_id,)
                )
            )
            (
                await conn.execute(
                    "DELETE FROM sessions WHERE session_id = %s", (session_id,)
                )
            )

            demoted_ids = await self._recalculate_experiences(
                conn,
                affected_ids,
                negative_impacts,
                conflict_impacts,
            )
            now = datetime.now(timezone.utc).isoformat()
            session_hash = self._identifier_hash(session_id)
            audit_id = str(uuid.uuid4())
            (
                await conn.execute(
                    """
                INSERT INTO session_deletion_audits
                    (audit_id, session_id_hash, user_id_hash,
                     deleted_message_count, deleted_feedback_count,
                     deleted_job_count, deleted_evaluation_count,
                     deleted_failure_count, deleted_trace_count,
                     affected_experience_ids, demoted_experience_ids,
                     cleanup_status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                """,
                    (
                        audit_id,
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
            )
            return {
                "audit_id": audit_id,
                "session_id_hash": session_hash,
                "affected_experience_ids": affected_ids,
                "demoted_experience_ids": demoted_ids,
            }

        return await self._execute(_do_delete)

    async def complete_session_cleanup(
        self,
        audit_id: str,
        errors: List[str],
    ) -> None:
        """记录数据库外部的向量和文件清理结果。"""

        async def _do_complete(conn: Connection):
            (
                await conn.execute(
                    """
                UPDATE session_deletion_audits
                SET cleanup_status = %s, cleanup_errors = %s,
                    cleanup_completed_at = %s
                WHERE audit_id = %s
                """,
                    (
                        "completed" if not errors else "partial",
                        json.dumps(errors, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(),
                        audit_id,
                    ),
                )
            )

        (await self._execute(_do_complete))

    @staticmethod
    def _identifier_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    async def _session_deletion_counts(
        conn: Connection,
        session_id: str,
    ) -> Dict[str, int]:
        queries = {
            "messages": "SELECT COUNT(*) AS count FROM messages WHERE session_id = %s",
            "feedback": """
                SELECT COUNT(*) AS count FROM conversation_feedback
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = %s)
            """,
            "jobs": """
                SELECT COUNT(*) AS count FROM evaluation_jobs
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = %s)
            """,
            "evaluations": """
                SELECT COUNT(*) AS count FROM conversation_evaluations
                WHERE assistant_message_id IN (
                    SELECT id FROM messages WHERE session_id = %s)
            """,
            "failures": """
                SELECT COUNT(*) AS count FROM failure_cases
                WHERE evaluation_id IN (
                    SELECT evaluations.evaluation_id
                    FROM conversation_evaluations AS evaluations
                    JOIN messages
                      ON messages.id = evaluations.assistant_message_id
                    WHERE messages.session_id = %s)
            """,
        }
        counts = {
            key: (await conn.execute(query, (session_id,))).fetchone()["count"]
            for key, query in queries.items()
        }
        counts["traces"] = (
            await conn.execute(
                "SELECT COUNT(*) AS count FROM traces WHERE session_id = %s",
                (session_id,),
            )
        ).fetchone()["count"]
        return counts

    async def _experience_impacts(
        self,
        conn: Connection,
        session_id: str,
    ) -> tuple[Dict[str, int], Dict[str, int]]:
        """计算待删除负反馈和失败评审对已应用经验的影响。"""
        negative: Dict[str, int] = {}
        conflicts: Dict[str, int] = {}
        rows = (
            await conn.execute(
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
            WHERE messages.session_id = %s AND messages.role = 'assistant'
            GROUP BY messages.id, feedback.rating
            """,
                (session_id,),
            )
        ).fetchall()
        for row in rows:
            applied_ids = await self._get_applied_experience_ids(conn, row["id"])
            for experience_id in applied_ids:
                if row["rating"] == "dislike":
                    negative[experience_id] = negative.get(experience_id, 0) + 1
                if row["failures"]:
                    conflicts[experience_id] = (
                        conflicts.get(experience_id, 0) + row["failures"]
                    )
        return negative, conflicts

    async def _recalculate_experiences(
        self,
        conn: Connection,
        source_experience_ids: List[str],
        negative_impacts: Dict[str, int],
        conflict_impacts: Dict[str, int],
    ) -> List[str]:
        """根据剩余评审证据重算经验统计并执行降级。"""
        all_ids = (
            set(source_experience_ids) | set(negative_impacts) | set(conflict_impacts)
        )
        demoted_ids: List[str] = []
        release_required = False
        now = datetime.now(timezone.utc).isoformat()
        for experience_id in all_ids:
            row = (
                await conn.execute(
                    "SELECT * FROM learned_experiences WHERE experience_id = %s",
                    (experience_id,),
                )
            ).fetchone()
            if row is None:
                continue
            aggregate = (
                await conn.execute(
                    """
                SELECT COUNT(*) AS support_count,
                       COALESCE(AVG(score), 0) AS average,
                       COUNT(DISTINCT user_id) AS distinct_users
                FROM experience_supports
                WHERE experience_id = %s
                """,
                    (experience_id,),
                )
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
            (
                await conn.execute(
                    """
                UPDATE learned_experiences
                SET support_count = %s, average_score = %s, distinct_users = %s,
                    negative_count = %s, conflict_count = %s, status = %s,
                    updated_at = %s
                WHERE experience_id = %s
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
            )
            refreshed = (
                await conn.execute(
                    "SELECT * FROM learned_experiences WHERE experience_id = %s",
                    (experience_id,),
                )
            ).fetchone()
            if status in {"active", "observing"}:
                try:
                    (self._validate_publication(refreshed))
                except ValueError:
                    status = "candidate"
                    (
                        await conn.execute(
                            "UPDATE learned_experiences SET status = %s WHERE experience_id = %s",
                            (status, experience_id),
                        )
                    )
            if row["status"] in {"active", "observing"} and status != row["status"]:
                demoted_ids.append(experience_id)
                release_required = True
        if release_required:
            (await self._create_release(conn, "auto_demote_session_deleted", "system"))
        return demoted_ids

    async def upsert_feedback(
        self,
        message_id: int,
        user_id: str,
        rating: str,
        reason_codes: List[str],
        comment: str,
    ) -> Dict[str, Any]:
        """新增或更新一条用户反馈。"""

        async def _do_upsert(conn: Connection):
            owned = (
                await conn.execute(
                    """
                SELECT 1 FROM messages AS m
                JOIN sessions AS s ON s.session_id = m.session_id
                WHERE m.id = %s AND m.role = 'assistant' AND s.user_id = %s
                """,
                    (message_id, user_id),
                )
            ).fetchone()
            if owned is None:
                raise LookupError("回答不存在")
            now = datetime.now(timezone.utc).isoformat()
            existing = (
                await conn.execute(
                    "SELECT * FROM conversation_feedback WHERE assistant_message_id = %s AND user_id = %s",
                    (message_id, user_id),
                )
            ).fetchone()
            if existing:
                previous_rating = existing["rating"]
                version = existing["version"] + 1
                (
                    await conn.execute(
                        """
                    UPDATE conversation_feedback
                    SET rating = %s, reason_codes = %s, comment = %s,
                        version = %s, updated_at = %s
                    WHERE feedback_id = %s
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
                )
                feedback_id = existing["feedback_id"]
            else:
                previous_rating = None
                feedback_id = str(uuid.uuid4())
                version = 1
                (
                    await conn.execute(
                        """
                    INSERT INTO conversation_feedback
                        (feedback_id, assistant_message_id, user_id, rating,
                         reason_codes, comment, version, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                )
            if previous_rating != rating:
                delta = (
                    1
                    if rating == "dislike"
                    else -1
                    if previous_rating == "dislike"
                    else 0
                )
                if delta:
                    applied_ids = await self._get_applied_experience_ids(
                        conn,
                        message_id,
                    )
                    for experience_id in applied_ids:
                        (
                            await conn.execute(
                                """
                            UPDATE learned_experiences
                            SET negative_count = GREATEST(0, negative_count + %s),
                                status = CASE
                                    WHEN %s > 0 AND status = 'observing'
                                    THEN 'retired'
                                    ELSE status
                                END,
                                updated_at = %s
                            WHERE experience_id = %s
                            """,
                                (delta, delta, now, experience_id),
                            )
                        )
                    if delta > 0 and applied_ids:
                        (
                            await self._create_release(
                                conn,
                                "auto_retire_negative_feedback",
                                "system",
                            )
                        )
            (
                await conn.execute(
                    """
                UPDATE evaluation_jobs
                SET status = 'superseded', updated_at = %s
                WHERE assistant_message_id = %s
                  AND trigger_type = 'user_feedback'
                  AND feedback_version < %s
                  AND status = 'pending'
                """,
                    (now, message_id, version),
                )
            )
            return {
                "feedback_id": feedback_id,
                "assistant_message_id": str(message_id),
                "rating": rating,
                "reason_codes": reason_codes,
                "comment": comment,
                "version": version,
            }

        return await self._execute(_do_upsert)

    @staticmethod
    async def _get_applied_experience_ids(
        conn: Connection,
        message_id: int,
    ) -> List[str]:
        """从结构化曝光记录读取回答实际应用的经验。"""
        rows = (
            await conn.execute(
                """
            SELECT experience_id FROM experience_exposures
            WHERE assistant_message_id = %s AND applied = 1
            """,
                (message_id,),
            )
        ).fetchall()
        return [row["experience_id"] for row in rows]

    async def record_exposures(
        self,
        message_id: int,
        user_id: str,
        assignments: List[Dict[str, Any]],
    ) -> None:
        """持久化本次回答的经验实验分组。"""

        async def _do_record(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()
            for assignment in assignments:
                bucket = assignment.get("bucket")
                if bucket not in {"active", "treatment", "control"}:
                    raise ValueError("非法经验实验分组")
                (
                    await conn.execute(
                        """
                    INSERT INTO experience_exposures
                        (experience_id, assistant_message_id, user_id, bucket,
                         applied, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        bucket = VALUES(bucket),
                        applied = VALUES(applied)
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
                )

        (await self._execute(_do_record))

    async def get_feedback(
        self, message_id: int, user_id: str
    ) -> Optional[Dict[str, Any]]:
        async def _do_get(conn: Connection):
            row = (
                await conn.execute(
                    "SELECT * FROM conversation_feedback WHERE assistant_message_id = %s AND user_id = %s",
                    (message_id, user_id),
                )
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["reason_codes"] = json.loads(result["reason_codes"])
            result["assistant_message_id"] = str(result["assistant_message_id"])
            return result

        return await self._execute(_do_get)

    async def enqueue_job(
        self,
        message_id: int,
        user_id: str,
        trigger_type: str,
        feedback_version: int = 0,
        feedback_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """幂等创建评审任务。"""

        async def _do_enqueue(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()
            job_id = str(uuid.uuid4())
            cursor = await conn.execute(
                """
                INSERT INTO evaluation_jobs
                    (job_id, assistant_message_id, user_id, trigger_type,
                     feedback_version, status, attempts, scheduled_at,
                     created_at, updated_at, feedback_snapshot)
                VALUES (%s, %s, %s, %s, %s, 'pending', 0, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE assistant_message_id = assistant_message_id
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
                    if feedback_snapshot
                    else None,
                ),
            )
            if cursor.rowcount:
                from mediZJ.infrastructure.jobs import enqueue

                await enqueue(
                    "evaluation", f"evaluation:{job_id}", {"evaluation_job_id": job_id}
                )
                return job_id
            return None

        return await self._execute(_do_enqueue)

    async def claim_job(self) -> Optional[Dict[str, Any]]:
        """评审同样通过统一任务表领取，并携带执行令牌。"""
        from mediZJ.infrastructure.jobs import claim, finish

        lease = await claim("evaluation", uuid.uuid4().hex)
        if lease is None:
            return None
        async with transaction() as conn:
            row = (
                await conn.execute(
                    "SELECT * FROM evaluation_jobs WHERE job_id=%s FOR UPDATE",
                    (lease["payload"]["evaluation_job_id"],),
                )
            ).fetchone()
            if row is None or row["status"] in {"superseded", "completed"}:
                await finish(lease)
                return await self.claim_job()
            await conn.execute(
                "UPDATE evaluation_jobs SET status='running',attempts=%s WHERE job_id=%s",
                (lease["attempts"], row["job_id"]),
            )
            return {**dict(row), "attempts": lease["attempts"], "_lease": lease}

    async def complete_job(self, job: dict) -> None:
        from mediZJ.infrastructure.jobs import finish

        await finish(job["_lease"])

    async def save_evaluation(
        self,
        job: Dict[str, Any],
        result: Dict[str, Any],
        judge_model: str,
    ) -> str:
        """保存评分，并生成失败案例或经验候选。"""

        async def _do_save(conn: Connection):
            if job.get("_lease"):
                from mediZJ.infrastructure.jobs import assert_lease

                await assert_lease(conn, job["_lease"])
            existing_evaluation = (
                await conn.execute(
                    "SELECT evaluation_id FROM conversation_evaluations WHERE job_id = %s",
                    (job["job_id"],),
                )
            ).fetchone()
            if existing_evaluation:
                (
                    await conn.execute(
                        "UPDATE evaluation_jobs SET status = 'completed', lease_until = NULL, updated_at = %s WHERE job_id = %s",
                        (datetime.now(timezone.utc).isoformat(), job["job_id"]),
                    )
                )
                return existing_evaluation["evaluation_id"]
            evaluation_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            overall = float(result["overall_score"])
            safety = bool(result.get("safety_violation", False))
            verdict = result.get("verdict") or (
                "low"
                if overall < 65 or safety
                else "high"
                if overall >= 85
                else "medium"
            )
            proposed_experiences = result.get("experiences") or (
                [result["experience"]] if result.get("experience") else []
            )
            experiences = [
                experience
                for experience in proposed_experiences
                if isinstance(experience, dict)
                and experience.get("type", "response_strategy") in _EXPERIENCE_TYPES
            ]
            is_superseded = False
            if (
                job.get("trigger_type") == "user_feedback"
                and int(job.get("feedback_version") or 0) > 0
            ):
                current_feedback = (
                    await conn.execute(
                        "SELECT version FROM conversation_feedback WHERE assistant_message_id = %s AND user_id = %s",
                        (job["assistant_message_id"], job["user_id"]),
                    )
                ).fetchone()
                is_superseded = (
                    current_feedback is None
                    or current_feedback["version"] != job["feedback_version"]
                )
            (
                await conn.execute(
                    """
                INSERT INTO conversation_evaluations
                    (evaluation_id, job_id, assistant_message_id, user_id,
                     overall_score, dimension_scores, verdict,
                     safety_violation, attribution, rationale,
                     recommendations, extracted_experience, judge_model,
                     rubric_version, is_superseded, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'v3', %s, %s)
                """,
                    (
                        evaluation_id,
                        job["job_id"],
                        job["assistant_message_id"],
                        job["user_id"],
                        overall,
                        json.dumps(
                            result.get("dimension_scores", {}), ensure_ascii=False
                        ),
                        verdict,
                        int(safety),
                        json.dumps(result.get("attribution", []), ensure_ascii=False),
                        result.get("rationale", ""),
                        json.dumps(
                            result.get("recommendations", []), ensure_ascii=False
                        ),
                        json.dumps(experiences, ensure_ascii=False),
                        judge_model,
                        int(is_superseded),
                        now,
                    ),
                )
            )
            if not is_superseded and (verdict == "low" or safety):
                (
                    await conn.execute(
                        """
                    INSERT INTO failure_cases
                        (failure_id, evaluation_id, user_id, root_causes,
                         evidence, recommended_fix, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'open', %s)
                    """,
                        (
                            str(uuid.uuid4()),
                            evaluation_id,
                            job["user_id"],
                            json.dumps(
                                result.get("attribution", ["other"]), ensure_ascii=False
                            ),
                            json.dumps(result.get("evidence", []), ensure_ascii=False),
                            "；".join(result.get("recommendations", [])),
                            now,
                        ),
                    )
                )
                applied_ids = await self._get_applied_experience_ids(
                    conn,
                    int(job["assistant_message_id"]),
                )
                supported_ids = [
                    row["experience_id"]
                    for row in (
                        await conn.execute(
                            "SELECT experience_id FROM experience_supports WHERE assistant_message_id = %s",
                            (job["assistant_message_id"],),
                        )
                    ).fetchall()
                ]
                (
                    await conn.execute(
                        "DELETE FROM experience_supports WHERE assistant_message_id = %s",
                        (job["assistant_message_id"],),
                    )
                )
                for experience_id in applied_ids:
                    (
                        await conn.execute(
                            """
                        UPDATE learned_experiences
                        SET conflict_count = conflict_count + 1,
                            status = CASE
                                WHEN status IN ('active', 'observing')
                                THEN 'retired'
                                ELSE status
                            END,
                            updated_at = %s
                        WHERE experience_id = %s
                        """,
                            (now, experience_id),
                        )
                    )
                if applied_ids:
                    (
                        await self._create_release(
                            conn,
                            "auto_retire_failed_evaluation",
                            "system",
                        )
                    )
                for experience_id in supported_ids:
                    (
                        await self._recompute_experience_statistics(
                            conn,
                            experience_id,
                            now,
                        )
                    )
            if not is_superseded and verdict == "high":
                for experience in experiences:
                    (
                        await self._upsert_experience(
                            conn,
                            evaluation_id,
                            job["user_id"],
                            overall,
                            experience,
                            int(job["assistant_message_id"]),
                            now,
                        )
                    )
            (
                await conn.execute(
                    """
                UPDATE evaluation_jobs
                SET status = 'completed', lease_until = NULL, updated_at = %s
                WHERE job_id = %s
                """,
                    (now, job["job_id"]),
                )
            )
            return evaluation_id

        return await self._execute(_do_save)

    async def _upsert_experience(
        self,
        conn: Connection,
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
        existing = (
            await conn.execute(
                """
            SELECT * FROM learned_experiences
            WHERE experience_type = %s AND scope = %s
              AND COALESCE(owner_user_id, '') = COALESCE(%s, '')
              AND query_pattern = %s
              AND status IN ('candidate', 'observing', 'active')
            ORDER BY version DESC LIMIT 1
            """,
                (experience_type, scope, owner, query_pattern),
            )
        ).fetchone()
        json_fields = {
            "applicability": experience.get("applicability", []),
            "exclusions": experience.get("exclusions", []),
            "prerequisites": experience.get("prerequisites", []),
            "evidence_refs": experience.get("evidence_refs", []),
        }
        if existing:
            experience_id = existing["experience_id"]
            (
                await conn.execute(
                    """
                UPDATE learned_experiences
                SET content = %s,
                    applicability = %s, exclusions = %s, prerequisites = %s,
                    safety_notes = %s, evidence_refs = %s, risk_level = %s,
                    capability_tag = %s, expires_at = %s,
                    last_validated_at = %s, updated_at = %s
                WHERE experience_id = %s
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
            )
        else:
            experience_id = str(uuid.uuid4())
            (
                await conn.execute(
                    """
                INSERT INTO learned_experiences
                    (experience_id, experience_type, scope, owner_user_id,
                     query_pattern, content, status, average_score,
                     support_count, conflict_count, version, applicability,
                     exclusions, prerequisites, safety_notes, evidence_refs,
                     risk_level, capability_tag, distinct_users,
                     negative_count, expires_at, last_validated_at,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'candidate', 0, 0, 0, 1, %s, %s, %s,
                        %s, %s, %s, %s, 0, 0, %s, %s, %s, %s)
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
            )
        (
            await conn.execute(
                "INSERT IGNORE INTO experience_sources VALUES (%s, %s)",
                (experience_id, evaluation_id),
            )
        )
        (
            await conn.execute(
                """
            INSERT INTO experience_supports
                (experience_id, assistant_message_id, evaluation_id, user_id,
                 score, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                evaluation_id = VALUES(evaluation_id),
                score = VALUES(score),
                created_at = VALUES(created_at)
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
        )
        (await self._recompute_experience_statistics(conn, experience_id, now))
        row = (
            await conn.execute(
                "SELECT * FROM learned_experiences WHERE experience_id = %s",
                (experience_id,),
            )
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
                (self._validate_publication(row))
            except ValueError:
                status = row["status"]
            else:
                status = "active"
        (
            await conn.execute(
                """
            UPDATE learned_experiences
            SET status = %s, updated_at = %s
            WHERE experience_id = %s
            """,
                (status, now, experience_id),
            )
        )
        if status == "active" and row["status"] != "active":
            (await self._create_release(conn, "auto_promote", "system"))

    @staticmethod
    async def _recompute_experience_statistics(
        conn: Connection,
        experience_id: str,
        now: str,
    ) -> None:
        aggregate = (
            await conn.execute(
                """
            SELECT COUNT(*) AS support_count,
                   COALESCE(AVG(score), 0) AS average_score,
                   COUNT(DISTINCT user_id) AS distinct_users
            FROM experience_supports
            WHERE experience_id = %s
            """,
                (experience_id,),
            )
        ).fetchone()
        (
            await conn.execute(
                """
            UPDATE learned_experiences
            SET support_count = %s, average_score = %s, distinct_users = %s,
                updated_at = %s
            WHERE experience_id = %s
            """,
                (
                    aggregate["support_count"],
                    aggregate["average_score"],
                    aggregate["distinct_users"],
                    now,
                    experience_id,
                ),
            )
        )

    async def list_evaluations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """返回可追溯到原对话、反馈和 Trace 的评审列表。"""

        async def _do_list(conn: Connection):
            rows = (
                await conn.execute(
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
                LIMIT %s
                """,
                    (limit,),
                )
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

        return await self._execute(_do_list)

    async def list_failures(self, limit: int = 100) -> List[Dict[str, Any]]:
        """返回关联对话、Trace 和源码位置的失败案例。"""

        async def _do_list(conn: Connection):
            rows = (
                await conn.execute(
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
                LIMIT %s
                """,
                    (limit,),
                )
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
                item["source_locations"] = get_source_locations(item["root_causes"])
                items.append(item)
            return items

        return await self._execute(_do_list)

    async def list_experiences(
        self,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        async def _do_list(conn: Connection):
            if status:
                rows = (
                    await conn.execute(
                        "SELECT * FROM learned_experiences WHERE status = %s ORDER BY updated_at DESC LIMIT %s",
                        (status, limit),
                    )
                ).fetchall()
            else:
                rows = (
                    await conn.execute(
                        "SELECT * FROM learned_experiences ORDER BY updated_at DESC LIMIT %s",
                        (limit,),
                    )
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
                metrics = await self._observation_metrics(conn, item["experience_id"])
                item["observation_metrics"] = metrics
                try:
                    (self._validate_observation(row))
                    item["eligible_for_observation"] = True
                    item["observation_blocker"] = ""
                except ValueError as exc:
                    item["eligible_for_observation"] = False
                    item["observation_blocker"] = str(exc)
                try:
                    (self._validate_activation(row, metrics))
                    item["eligible_for_activation"] = True
                    item["activation_blocker"] = ""
                except ValueError as exc:
                    item["eligible_for_activation"] = False
                    item["activation_blocker"] = str(exc)
                uses_observation_gate = (
                    item["status"] == "candidate" and item["scope"] == "global"
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

        return await self._execute(_do_list)

    async def apply_experience_action(
        self,
        experience_id: str,
        action: str,
        operator_user_id: str,
    ) -> bool:
        """执行观察、发布、驳回、重新应用、退役或删除动作。"""

        async def _do_set(conn: Connection):
            row = (
                await conn.execute(
                    "SELECT * FROM learned_experiences WHERE experience_id = %s",
                    (experience_id,),
                )
            ).fetchone()
            if row is None:
                return False
            if action == "delete":
                if row["status"] != "rejected":
                    raise ValueError("仅已驳回的经验可以删除")
                cursor = await conn.execute(
                    "DELETE FROM learned_experiences WHERE experience_id = %s",
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
                (self._validate_observation(row))
            elif action == "activate":
                if row["scope"] == "global":
                    if row["status"] != "observing":
                        raise ValueError("全局经验必须先完成观察")
                    metrics = await self._observation_metrics(conn, experience_id)
                    (self._validate_activation(row, metrics))
                else:
                    (self._validate_publication(row))
            elif action == "reject" and row["status"] not in {
                "candidate",
                "retired",
            }:
                raise ValueError("仅待审核或已停用的经验可以驳回")
            elif action == "reapply" and row["status"] != "rejected":
                raise ValueError("仅已驳回的经验可以重新应用")
            cursor = await conn.execute(
                "UPDATE learned_experiences SET status = %s, updated_at = %s WHERE experience_id = %s",
                (target_status, datetime.now(timezone.utc).isoformat(), experience_id),
            )
            if not cursor.rowcount:
                return False
            if target_status in {"active", "observing", "retired"}:
                (await self._create_release(conn, action, operator_user_id))
            return True

        return await self._execute(_do_set)

    async def set_experience_status(
        self,
        experience_id: str,
        status: str,
        operator_user_id: str,
    ) -> bool:
        """兼容内部调用，并映射为明确治理动作。"""

        async def get_row(conn):
            return (
                await conn.execute(
                    "SELECT scope, status FROM learned_experiences WHERE experience_id = %s",
                    (experience_id,),
                )
            ).fetchone()

        row = await self._execute(get_row)
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
        return await self.apply_experience_action(
            experience_id,
            action,
            operator_user_id,
        )

    @staticmethod
    async def _observation_metrics(
        conn: Connection,
        experience_id: str,
    ) -> Dict[str, Any]:
        rows = (
            await conn.execute(
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
            WHERE exposures.experience_id = %s
              AND exposures.bucket IN ('treatment', 'control')
            GROUP BY exposures.bucket
            """,
                (experience_id,),
            )
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
    def _validate_observation(row: dict[str, Any]) -> None:
        (EvolutionStorage._validate_publication(row))
        if row["scope"] != "global":
            raise ValueError("仅全局经验需要观察")

    @staticmethod
    def _validate_activation(
        row: dict[str, Any],
        metrics: Dict[str, Any],
    ) -> None:
        (EvolutionStorage._validate_publication(row))
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
    def _validate_publication(row: dict[str, Any]) -> None:
        """强制校验经验的发布证据与医疗安全边界。"""
        if row["conflict_count"] > 0:
            raise ValueError("存在冲突案例，不能发布")
        if row["negative_count"] > 0:
            raise ValueError("存在负面反馈，不能发布")
        if row["scope"] == "global" and (EvolutionStorage._row_has_personal_data(row)):
            raise ValueError("全局经验包含个人身份信息")
        evidence_refs = json.loads(row["evidence_refs"] or "[]")
        prerequisites = json.loads(row["prerequisites"] or "[]")
        settings = EvolutionSettings.from_env()
        trusted_evidence = [
            evidence
            for evidence in evidence_refs
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
            if expires_at <= datetime.now(timezone.utc):
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
                "全局经验至少需 %d 个不同用户的支持案例" % settings.global_min_support
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
        return parsed.scheme == "https" and hostname in settings.trusted_domains

    @staticmethod
    def _row_has_personal_data(row: dict[str, Any]) -> bool:
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
        return any((re.search(pattern, text)) for pattern in patterns)

    @staticmethod
    async def _create_release(
        conn: Connection,
        action: str,
        operator_user_id: str,
    ) -> None:
        row = (
            await conn.execute("SELECT MAX(version) AS version FROM strategy_releases")
        ).fetchone()
        previous = row["version"] if row and row["version"] else None
        version = (previous or 0) + 1
        active = (
            await conn.execute(
                "SELECT experience_id, status FROM learned_experiences "
                "WHERE status IN ('active', 'observing') ORDER BY experience_id"
            )
        ).fetchall()
        (
            await conn.execute(
                """
            INSERT INTO strategy_releases
                (release_id, version, active_ids, previous_version,
                 action, operator_user_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    str(uuid.uuid4()),
                    version,
                    json.dumps({row["experience_id"]: row["status"] for row in active}),
                    previous,
                    action,
                    operator_user_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        )

    async def list_releases(self, limit: int = 50) -> List[Dict[str, Any]]:
        async def _do_list(conn: Connection):
            rows = (
                await conn.execute(
                    "SELECT * FROM strategy_releases ORDER BY version DESC LIMIT %s",
                    (limit,),
                )
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._execute(_do_list)

    async def list_jobs(
        self,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按状态返回评审任务。"""

        async def _do_list(conn: Connection):
            if status:
                rows = (
                    await conn.execute(
                        "SELECT * FROM evaluation_jobs WHERE status = %s ORDER BY updated_at DESC LIMIT %s",
                        (status, limit),
                    )
                ).fetchall()
            else:
                rows = (
                    await conn.execute(
                        "SELECT * FROM evaluation_jobs ORDER BY updated_at DESC LIMIT %s",
                        (limit,),
                    )
                ).fetchall()
            return [dict(row) for row in rows]

        return await self._execute(_do_list)

    async def retry_job(self, job_id: str) -> bool:
        """将失败任务重新加入队列。"""

        async def _do_retry(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()
            cursor = await conn.execute(
                """
                UPDATE evaluation_jobs
                SET status = 'pending', attempts = 0, scheduled_at = %s,
                    lease_until = NULL, last_error = NULL, updated_at = %s
                WHERE job_id = %s AND status = 'failed'
                """,
                (now, now, job_id),
            )
            return bool(cursor.rowcount)

        return await self._execute(_do_retry)

    async def rollback_release(self, version: int, operator_user_id: str) -> bool:
        async def _do_rollback(conn: Connection):
            row = (
                await conn.execute(
                    "SELECT active_ids FROM strategy_releases WHERE version = %s",
                    (version,),
                )
            ).fetchone()
            if row is None:
                return False
            active_snapshot = json.loads(row["active_ids"])
            if isinstance(active_snapshot, list):
                active_snapshot = {
                    experience_id: "active" for experience_id in active_snapshot
                }
            blockers = []
            for experience_id, status in active_snapshot.items():
                experience = (
                    await conn.execute(
                        "SELECT * FROM learned_experiences WHERE experience_id = %s",
                        (experience_id,),
                    )
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
                        metrics = await self._observation_metrics(conn, experience_id)
                        (self._validate_activation(experience, metrics))
                    else:
                        (self._validate_publication(experience))
                except ValueError as exc:
                    blockers.append(
                        {
                            "experience_id": experience_id,
                            "blocker": str(exc),
                        }
                    )
            if blockers:
                raise RollbackBlockedError(blockers)
            (
                await conn.execute(
                    "UPDATE learned_experiences SET status = 'retired' "
                    "WHERE status IN ('active', 'observing')"
                )
            )
            for experience_id, status in active_snapshot.items():
                (
                    await conn.execute(
                        "UPDATE learned_experiences SET status = %s WHERE experience_id = %s",
                        (status, experience_id),
                    )
                )
            (await self._create_release(conn, f"rollback:{version}", operator_user_id))
            return True

        return await self._execute(_do_rollback)

    async def get_active_experiences(
        self,
        user_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """返回当前用户可用的私有和全局经验。"""

        async def _do_get(conn: Connection):
            now = datetime.now(timezone.utc).isoformat()
            cursor = await conn.execute(
                """
                UPDATE learned_experiences
                SET status = 'candidate', updated_at = %s
                WHERE status IN ('active', 'observing')
                  AND expires_at IS NOT NULL AND expires_at <= %s
                """,
                (now, now),
            )
            if cursor.rowcount:
                (await self._create_release(conn, "auto_expire", "system"))
            sql = """
                SELECT * FROM learned_experiences
                WHERE status IN ('active', 'observing')
                  AND (expires_at IS NULL OR expires_at > %s)
                  AND (scope = 'global' OR owner_user_id = %s)
                ORDER BY CASE WHEN owner_user_id = %s THEN 0 ELSE 1 END,
                         average_score DESC
            """
            params: List[Any] = [now, user_id, user_id]
            if limit is not None:
                sql += " LIMIT %s"
                params.append(limit)
            rows = (await conn.execute(sql, tuple(params))).fetchall()
            return [dict(row) for row in rows]

        return await self._execute(_do_get)

    async def overview(self) -> Dict[str, Any]:
        async def _do_overview(conn: Connection):
            eval_row = (
                await conn.execute(
                    "SELECT COUNT(*) AS count, AVG(overall_score) AS average "
                    "FROM conversation_evaluations"
                )
            ).fetchone()
            job_rows = (
                await conn.execute(
                    "SELECT status, COUNT(*) AS count FROM evaluation_jobs "
                    "GROUP BY status"
                )
            ).fetchall()
            job_counts = {row["status"]: row["count"] for row in job_rows}
            exposure_rows = (
                await conn.execute(
                    """
                SELECT bucket, COUNT(*) AS count,
                       COUNT(DISTINCT user_id) AS distinct_users
                FROM experience_exposures
                GROUP BY bucket
                """
                )
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
                "failure_count": (
                    await conn.execute("SELECT COUNT(*) AS count FROM failure_cases")
                ).fetchone()["count"],
                "candidate_count": (
                    await conn.execute(
                        "SELECT COUNT(*) AS count FROM learned_experiences "
                        "WHERE status = 'candidate'"
                    )
                ).fetchone()["count"],
                "active_count": (
                    await conn.execute(
                        "SELECT COUNT(*) AS count FROM learned_experiences "
                        "WHERE status = 'active'"
                    )
                ).fetchone()["count"],
                "observing_count": (
                    await conn.execute(
                        "SELECT COUNT(*) AS count FROM learned_experiences "
                        "WHERE status = 'observing'"
                    )
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

        return await self._execute(_do_overview)
