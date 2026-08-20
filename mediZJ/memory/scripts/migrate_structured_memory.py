"""将旧 profiles 与 memory_lineage 原子迁移到结构化记忆。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from mediZJ.memory.personal_profile import PersonalProfile
from mediZJ.memory.prompt_prefix import PromptPrefixAssembler, stable_hash
from mediZJ.memory.session_db import SessionDB


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _source_for(
    conn: sqlite3.Connection,
    user_id: str,
    memory_kind: str,
    legacy_key: str,
) -> dict[str, Any]:
    key_hash = hashlib.sha256(legacy_key.encode("utf-8")).hexdigest()
    try:
        row = conn.execute(
            """
            SELECT * FROM memory_lineage
            WHERE owner_user_id = ? AND memory_kind = ? AND memory_key_hash = ?
            """,
            (user_id, memory_kind, key_hash),
        ).fetchone()
    except sqlite3.OperationalError:
        return {}
    return dict(row) if row else {}


def migrate(db_path: str, dry_run: bool = False) -> dict[str, int]:
    """在单个事务中迁移所有旧档案。"""
    SessionDB.reset()
    db = SessionDB(db_path)
    conn = db._get_conn()
    conn.execute("BEGIN IMMEDIATE")
    parser = PersonalProfile.__new__(PersonalProfile)
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "users": 0,
        "active_profile_facts": 0,
        "active_medical_records": 0,
        "pending_items": 0,
    }
    try:
        rows = conn.execute(
            "SELECT user_id, content, pending FROM profiles ORDER BY user_id"
        ).fetchall()
        for row in rows:
            user_id = row["user_id"]
            conn.execute(
                """
                INSERT INTO users (
                    user_id, username, username_normalized, role,
                    is_active, created_at
                ) VALUES (?, ?, ?, 'user', 1, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id, user_id, user_id.casefold(), now),
            )
            exists = conn.execute(
                "SELECT 1 FROM user_memory_items WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if exists:
                continue
            report["users"] += 1
            parsed = parser._parse_profile(row["content"])
            active_items: list[tuple[str, str, Any, str, str]] = []
            for key, value in sorted(parsed["confirmed"].items()):
                active_items.append(
                    ("profile_fact", key, value, "profile", f"{key}:{value}")
                )
                report["active_profile_facts"] += 1
            for record in parsed["records"]:
                value = {
                    "date": record.date,
                    "description": record.description,
                    "symptoms": record.symptoms,
                    "duration": record.duration,
                    "medication": record.medication,
                    "outcome": record.outcome,
                }
                key = f"{record.date}:{record.description}"
                active_items.append(
                    ("medical_record", key, value, "profile_record", key)
                )
                report["active_medical_records"] += 1
            pending_items = parser._parse_legacy_pending(row["pending"])
            for memory_type, key, value, kind, legacy_key in active_items:
                lineage = _source_for(conn, user_id, kind, legacy_key)
                lineage_source = str(lineage.get("source_type") or "")
                source_type = {
                    "model_inferred": "model_inferred",
                    "authoritative_document": "report_extracted",
                }.get(lineage_source, "user_reported")
                conn.execute(
                    """
                    INSERT INTO user_memory_items (
                        memory_id, user_id, memory_type, memory_key,
                        value_json, status, source_type, confidence,
                        sensitivity_level, consent_scope,
                        source_message_id, source_trace_id, effective_at,
                        expires_at, revision, created_at, updated_at,
                        confirmed_at
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, 1.0, 'sensitive',
                              'personalization', ?, ?, NULL, ?, 1, ?, ?, ?)
                    """,
                    (
                        "memory_" + uuid.uuid4().hex,
                        user_id,
                        memory_type,
                        key,
                        _canonical_json(value),
                        source_type,
                        lineage.get("source_message_id"),
                        lineage.get("source_trace_id"),
                        lineage.get("valid_until"),
                        now,
                        now,
                        now,
                    ),
                )
            for pending_item in pending_items:
                memory_type = (
                    "medical_record" if pending_item.is_record else "profile_fact"
                )
                key = (
                    f"{pending_item.record_date}:{pending_item.value}"
                    if pending_item.is_record
                    else pending_item.key
                )
                value = (
                    {
                        "date": pending_item.record_date,
                        "description": pending_item.value,
                        "symptoms": pending_item.symptoms,
                        "duration": pending_item.duration,
                        "medication": pending_item.medication,
                        "outcome": pending_item.outcome,
                    }
                    if pending_item.is_record
                    else pending_item.value
                )
                conn.execute(
                    """
                    INSERT INTO user_memory_items (
                        memory_id, user_id, memory_type, memory_key,
                        value_json, status, source_type, confidence,
                        sensitivity_level, consent_scope, revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 'model_inferred', ?,
                              'sensitive', 'none', 1, ?, ?)
                    """,
                    (
                        "memory_" + uuid.uuid4().hex,
                        user_id,
                        memory_type,
                        key,
                        _canonical_json(value),
                        0.9
                        if pending_item.confidence in {"high", "confirmed"}
                        else 0.6,
                        now,
                        now,
                    ),
                )
                report["pending_items"] += 1
            memories = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT memory_id, memory_type, memory_key, value_json
                    FROM user_memory_items
                    WHERE user_id = ? AND status = 'active'
                    ORDER BY memory_type, memory_key, memory_id
                    """,
                    (user_id,),
                ).fetchall()
            ]
            for memory in memories:
                memory["value"] = json.loads(memory.pop("value_json"))
            prefix_hash = stable_hash(PromptPrefixAssembler.user_prefix(memories))
            conn.execute(
                """
                INSERT INTO memory_profile_revisions (
                    user_id, profile_revision, profile_prefix_hash, updated_at
                ) VALUES (?, 1, ?, ?)
                """,
                (user_id, prefix_hash, now),
            )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return report
    except Exception:
        conn.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(_canonical_json(migrate(args.db_path, args.dry_run)))


if __name__ == "__main__":
    main()
