"""结构化用户记忆、情景摘要及审计存储。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .session_db import SessionDB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _is_effective(effective_at: Optional[str], expires_at: Optional[str]) -> bool:
    now = datetime.now(timezone.utc)
    effective = _parse_timestamp(effective_at)
    expires = _parse_timestamp(expires_at)
    if effective_at and effective is None:
        return False
    if expires_at and expires is None:
        return False
    if effective and effective > now:
        return False
    if expires and expires <= now:
        return False
    return True


def _is_current(value: Optional[str]) -> bool:
    if not value:
        return True
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return False
    return timestamp > datetime.now(timezone.utc)


class StructuredMemoryStore:
    """结构化记忆的唯一写入入口。"""

    def __init__(self, db: Optional[SessionDB] = None) -> None:
        self.db = db or SessionDB()

    async def list_items(
        self,
        user_id: str,
        *,
        statuses: Iterable[str] = ("active",),
        memory_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        status_list = tuple(statuses)
        if not status_list:
            return []

        async def _select(conn):
            placeholders = ",".join("%s" for _ in status_list)
            sql = f"SELECT * FROM user_memory_items WHERE user_id = %s AND status IN ({placeholders})"
            params: list[Any] = [user_id, *status_list]
            if memory_type:
                sql += " AND memory_type = %s"
                params.append(memory_type)
            sql += " ORDER BY memory_type, memory_key, memory_id"
            return [dict(row) for row in (await conn.execute(sql, params)).fetchall()]

        rows = await self.db._execute(_select)
        result = []
        for row in rows:
            if row["status"] == "active" and not _is_effective(
                row["effective_at"], row["expires_at"]
            ):
                continue
            row["value"] = json.loads(row.pop("value_json"))
            result.append(row)
        return result

    async def upsert_active(
        self,
        user_id: str,
        memory_type: str,
        memory_key: str,
        value: Any,
        *,
        source_type: str = "user_reported",
        sensitivity_level: str = "sensitive",
        consent_scope: str = "personalization",
        actor_id: Optional[str] = None,
    ) -> str:
        now = _now()
        memory_id = "memory_" + uuid.uuid4().hex

        async def _upsert(conn):
            (await self._ensure_owner(conn, user_id, now))
            current = (
                await conn.execute(
                    """
                SELECT memory_id, revision, value_json, source_type,
                       sensitivity_level, consent_scope
                FROM user_memory_items
                WHERE user_id = %s AND memory_type = %s AND memory_key = %s
                  AND status = 'active'
                """,
                    (user_id, memory_type, memory_key),
                )
            ).fetchone()
            serialized = _canonical_json(value)
            source_rank = {
                "model_inferred": 0,
                "report_extracted": 1,
                "user_reported": 2,
                "clinician_confirmed": 3,
            }
            if current and source_rank.get(current["source_type"], 0) > source_rank.get(
                source_type, 0
            ):
                return current["memory_id"]
            if current and all(
                (
                    current["value_json"] == serialized,
                    current["source_type"] == source_type,
                    current["sensitivity_level"] == sensitivity_level,
                    current["consent_scope"] == consent_scope,
                )
            ):
                return current["memory_id"]
            revision = int(current["revision"]) + 1 if current else 1
            supersedes_id = current["memory_id"] if current else None
            if current:
                (
                    await conn.execute(
                        """
                    UPDATE user_memory_items
                    SET status = 'superseded', updated_at = %s
                    WHERE memory_id = %s
                    """,
                        (now, current["memory_id"]),
                    )
                )
            (
                await conn.execute(
                    """
                INSERT INTO user_memory_items (
                    memory_id, user_id, memory_type, memory_key, value_json,
                    status, source_type, confidence, sensitivity_level,
                    consent_scope, revision, supersedes_id, created_at,
                    updated_at, confirmed_at
                ) VALUES (%s, %s, %s, %s, %s, 'active', %s, 1.0, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        memory_id,
                        user_id,
                        memory_type,
                        memory_key,
                        serialized,
                        source_type,
                        sensitivity_level,
                        consent_scope,
                        revision,
                        supersedes_id,
                        now,
                        now,
                        now,
                    ),
                )
            )
            (await self._increment_revision(conn, user_id, now))
            (
                await self._audit(
                    conn,
                    memory_id,
                    user_id,
                    "activate",
                    actor_id or user_id,
                    {"memory_type": memory_type, "memory_key": memory_key},
                    now,
                )
            )
            return memory_id

        return await self.db._execute(_upsert)

    async def replace_active(
        self,
        user_id: str,
        memory_type: str,
        values: dict[str, Any],
        *,
        actor_id: Optional[str] = None,
    ) -> None:
        current = {
            item["memory_key"]: item["value"]
            for item in (
                await self.list_items(
                    user_id, statuses=("active",), memory_type=memory_type
                )
            )
        }
        for key in sorted(set(current) - set(values)):
            (await self.deactivate(user_id, memory_type, key, actor_id=actor_id))
        for key, value in sorted(values.items()):
            (
                await self.upsert_active(
                    user_id,
                    memory_type,
                    key,
                    value,
                    actor_id=actor_id,
                )
            )

    async def add_pending(
        self,
        user_id: str,
        memory_type: str,
        memory_key: str,
        value: Any,
        *,
        confidence: float = 0.5,
        source_message_id: Optional[str] = None,
        source_trace_id: Optional[str] = None,
    ) -> str:
        existing = await self.list_items(
            user_id,
            statuses=("pending",),
            memory_type=memory_type,
        )
        serialized = _canonical_json(value)
        for item in existing:
            if (
                item["memory_key"] == memory_key
                and _canonical_json(item["value"]) == serialized
            ):
                return item["memory_id"]
        memory_id = "memory_" + uuid.uuid4().hex
        now = _now()

        async def _insert(conn):
            (await self._ensure_owner(conn, user_id, now))
            (
                await conn.execute(
                    """
                INSERT INTO user_memory_items (
                    memory_id, user_id, memory_type, memory_key, value_json,
                    status, source_type, confidence, sensitivity_level,
                    consent_scope, source_message_id, source_trace_id,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, 'pending', 'model_inferred', %s,
                          'sensitive', 'none', %s, %s, %s, %s)
                """,
                    (
                        memory_id,
                        user_id,
                        memory_type,
                        memory_key,
                        serialized,
                        confidence,
                        source_message_id,
                        source_trace_id,
                        now,
                        now,
                    ),
                )
            )
            (
                await self._audit(
                    conn,
                    memory_id,
                    user_id,
                    "create_pending",
                    "system",
                    {},
                    now,
                )
            )

        (await self.db._execute(_insert))
        return memory_id

    async def confirm_pending(
        self, user_id: str, memory_key: str, expected_value: str
    ) -> bool:
        pending = await self.list_items(user_id, statuses=("pending",))
        match = next(
            (
                item
                for item in pending
                if item["memory_key"] == memory_key
                and self._display_value(item["value"]) == expected_value
            ),
            None,
        )
        if match is None:
            return False
        (
            await self.upsert_active(
                user_id,
                match["memory_type"],
                match["memory_key"],
                match["value"],
                source_type="user_reported",
                actor_id=user_id,
            )
        )
        (await self._set_status(match["memory_id"], user_id, "superseded", "confirm"))
        return True

    async def dismiss_pending(
        self, user_id: str, memory_key: str, expected_value: str
    ) -> bool:
        pending = await self.list_items(user_id, statuses=("pending",))
        match = next(
            (
                item
                for item in pending
                if item["memory_key"] == memory_key
                and self._display_value(item["value"]) == expected_value
            ),
            None,
        )
        if match is None:
            return False
        (await self._set_status(match["memory_id"], user_id, "dismissed", "dismiss"))
        return True

    async def deactivate(
        self,
        user_id: str,
        memory_type: str,
        memory_key: str,
        *,
        actor_id: Optional[str] = None,
    ) -> bool:
        now = _now()

        async def _deactivate(conn):
            row = (
                await conn.execute(
                    """
                SELECT memory_id FROM user_memory_items
                WHERE user_id = %s AND memory_type = %s AND memory_key = %s
                  AND status = 'active'
                """,
                    (user_id, memory_type, memory_key),
                )
            ).fetchone()
            if row is None:
                return False
            (
                await conn.execute(
                    """
                UPDATE user_memory_items
                SET status = 'superseded', updated_at = %s WHERE memory_id = %s
                """,
                    (now, row["memory_id"]),
                )
            )
            (await self._increment_revision(conn, user_id, now))
            (
                await self._audit(
                    conn,
                    row["memory_id"],
                    user_id,
                    "deactivate",
                    actor_id or user_id,
                    {},
                    now,
                )
            )
            return True

        return await self.db._execute(_deactivate)

    async def get_profile_revision(self, user_id: str) -> int:
        async def _get(conn):
            row = (
                await conn.execute(
                    """
                SELECT profile_revision FROM memory_profile_revisions
                WHERE user_id = %s
                """,
                    (user_id,),
                )
            ).fetchone()
            return int(row["profile_revision"]) if row else 0

        return await self.db._execute(_get)

    async def set_profile_hash(self, user_id: str, profile_hash: str) -> None:
        now = _now()

        async def _set(conn):
            (
                await conn.execute(
                    """
                INSERT INTO memory_profile_revisions (
                    user_id, profile_revision, profile_prefix_hash, updated_at
                ) VALUES (%s, 0, %s, %s)
                ON DUPLICATE KEY UPDATE
                    profile_prefix_hash = VALUES(profile_prefix_hash),
                    updated_at = VALUES(updated_at)
                """,
                    (user_id, profile_hash, now),
                )
            )

        (await self.db._execute(_set))

    async def save_episodic_summary(
        self,
        session_id: str,
        user_id: str,
        summary: str,
        resolved_entities: Optional[dict[str, Any]] = None,
        retention_days: int = 180,
    ) -> str:
        summary_id = "episode_" + uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(days=retention_days)).isoformat()

        async def _save(conn):
            (await self._ensure_owner(conn, user_id, now.isoformat()))
            existing = (
                await conn.execute(
                    "SELECT summary_id FROM episodic_summaries WHERE session_id = %s",
                    (session_id,),
                )
            ).fetchone()
            resolved_id = existing["summary_id"] if existing else summary_id
            (
                await conn.execute(
                    """
                INSERT INTO episodic_summaries (
                    summary_id, session_id, user_id, summary,
                    resolved_entities, status, created_at, updated_at,
                    expires_at
                ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    summary = VALUES(summary),
                    resolved_entities = VALUES(resolved_entities),
                    status = 'active', updated_at = VALUES(updated_at),
                    expires_at = VALUES(expires_at)
                """,
                    (
                        resolved_id,
                        session_id,
                        user_id,
                        summary,
                        _canonical_json(resolved_entities or {}),
                        now.isoformat(),
                        now.isoformat(),
                        expires_at,
                    ),
                )
            )
            return resolved_id

        return await self.db._execute(_save)

    async def recall_episodes(
        self, user_id: str, current_session_id: str
    ) -> list[dict[str, Any]]:
        async def _select(conn):
            rows = (
                await conn.execute(
                    """
                SELECT * FROM episodic_summaries
                WHERE user_id = %s AND session_id != %s AND status = 'active'
                ORDER BY updated_at DESC, summary_id
                """,
                    (user_id, current_session_id),
                )
            ).fetchall()
            return [dict(row) for row in rows]

        result = []
        for row in await self.db._execute(_select):
            if not _is_current(row["expires_at"]):
                continue
            row["resolved_entities"] = json.loads(row["resolved_entities"])
            result.append(row)
        return result

    async def record_usage(
        self,
        memory_ids: Iterable[str],
        *,
        session_id: str,
        trace_id: str,
        agent_id: str,
        user_id: str,
    ) -> None:
        now = _now()

        async def _record(conn):
            await conn.executemany(
                """
                INSERT INTO memory_usage (
                    usage_id, memory_id, session_id, trace_id,
                    agent_id, user_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        "usage_" + uuid.uuid4().hex,
                        memory_id,
                        session_id,
                        trace_id,
                        agent_id,
                        user_id,
                        now,
                    )
                    for memory_id in memory_ids
                ],
            )

        (await self.db._execute(_record))

    async def _set_status(
        self, memory_id: str, user_id: str, status: str, action: str
    ) -> None:
        now = _now()

        async def _update(conn):
            (
                await conn.execute(
                    """
                UPDATE user_memory_items SET status = %s, updated_at = %s
                WHERE memory_id = %s AND user_id = %s
                """,
                    (status, now, memory_id, user_id),
                )
            )
            (await self._audit(conn, memory_id, user_id, action, user_id, {}, now))

        (await self.db._execute(_update))

    @staticmethod
    async def _ensure_owner(conn, user_id: str, now: str) -> None:
        (
            await conn.execute(
                """
            INSERT INTO users (
                user_id, username, username_normalized, role,
                is_active, created_at
            ) VALUES (%s, %s, %s, 'user', 1, %s)
            ON DUPLICATE KEY UPDATE user_id = user_id
            """,
                (user_id, user_id, user_id.casefold(), now),
            )
        )

        await conn.execute(
            "SELECT user_id FROM users WHERE user_id=%s FOR UPDATE", (user_id,)
        )

    @staticmethod
    async def _increment_revision(conn, user_id: str, now: str) -> None:
        (
            await conn.execute(
                """
            INSERT INTO memory_profile_revisions (
                user_id, profile_revision, profile_prefix_hash, updated_at
            ) VALUES (%s, 1, '', %s)
            ON DUPLICATE KEY UPDATE
                profile_revision = profile_revision + 1,
                profile_prefix_hash = '', updated_at = VALUES(updated_at)
            """,
                (user_id, now),
            )
        )

    @staticmethod
    async def _audit(
        conn,
        memory_id: Optional[str],
        user_id: str,
        action: str,
        actor_id: str,
        detail: dict[str, Any],
        now: str,
    ) -> None:
        (
            await conn.execute(
                """
            INSERT INTO memory_audit (
                audit_id, memory_id, user_id, action,
                actor_id, detail_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    "audit_" + uuid.uuid4().hex,
                    memory_id,
                    user_id,
                    action,
                    actor_id,
                    _canonical_json(detail),
                    now,
                ),
            )
        )

    @staticmethod
    def _display_value(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("description") or value.get("value") or "")
        return str(value)
