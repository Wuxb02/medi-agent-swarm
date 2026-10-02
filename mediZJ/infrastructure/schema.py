"""MySQL 业务表定义；仅由 Alembic 创建，不在应用启动时建表。"""

from sqlalchemy import (
    BigInteger,
    JSON,
    Column,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import LONGTEXT, DATETIME as DateTime

metadata = MetaData()

metric_counters = Table(
    "metric_counters",
    metadata,
    Column("name", String(64), primary_key=True),
    Column("value", BigInteger, nullable=False, server_default="0"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

sessions = Table(
    "sessions",
    metadata,
    Column("session_id", String(191), primary_key=True),
    Column("user_id", String(191), nullable=False, server_default=text("'default'")),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Column(
        "mode", LONGTEXT, nullable=True, server_default=text("(" + "'single'" + ")")
    ),
    Column(
        "first_question", LONGTEXT, nullable=True, server_default=text("(" + "''" + ")")
    ),
    Column("total_tokens", Integer, nullable=True, server_default=text("0")),
    Column("message_count", Integer, nullable=True, server_default=text("0")),
    Column("turn_count", Integer, nullable=True, server_default=text("0")),
    Column("parallel_efficiency", Float, nullable=True, server_default=text("0")),
    Column("information_coverage", Float, nullable=True, server_default=text("0")),
    Column("redundancy", Float, nullable=True, server_default=text("0")),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "session_id",
        String(191),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("turn_index", Integer, nullable=False),
    Column("role", String(191), nullable=False),
    Column("content", LONGTEXT, nullable=False),
    Column("timestamp", String(191), nullable=False),
    Column("images", LONGTEXT, nullable=True),
    Column("agent_events", LONGTEXT, nullable=True),
    Column("suggestions", LONGTEXT, nullable=True),
    Column("agents_involved", LONGTEXT, nullable=True),
    Column("total_time", Float, nullable=True, server_default=text("0")),
    Column("total_tokens", Integer, nullable=True, server_default=text("0")),
    Column("subtasks_completed", Integer, nullable=True, server_default=text("0")),
    Column("mode", LONGTEXT, nullable=True),
    Column("citations", LONGTEXT, nullable=True),
    Column("trace_id", String(191), nullable=True),
    Index("idx_msg_session", "session_id", "turn_index"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

profiles = Table(
    "profiles",
    metadata,
    Column("user_id", String(191), primary_key=True),
    Column("content", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")),
    Column("pending", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")),
    Column("updated_at", String(191), nullable=False, server_default=text("''")),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

user_memory_items = Table(
    "user_memory_items",
    metadata,
    Column("memory_id", String(191), primary_key=True),
    Column(
        "user_id",
        String(191),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("memory_type", String(191), nullable=False),
    Column("memory_key", String(191), nullable=False),
    Column("value_json", LONGTEXT, nullable=False),
    Column("status", String(191), nullable=False),
    Column("source_type", String(191), nullable=False),
    Column("confidence", Float, nullable=False, server_default=text("1.0")),
    Column(
        "sensitivity_level",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'sensitive'" + ")"),
    ),
    Column(
        "consent_scope",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'none'" + ")"),
    ),
    Column("source_message_id", String(191), nullable=True),
    Column("source_trace_id", String(191), nullable=True),
    Column("effective_at", String(191), nullable=True),
    Column("expires_at", String(191), nullable=True),
    Column("revision", Integer, nullable=False, server_default=text("1")),
    Column("supersedes_id", String(191), nullable=True),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Column("confirmed_at", LONGTEXT, nullable=True),
    Index("idx_user_memory_recall", "user_id", "status", "memory_type", "memory_key"),
    Column(
        "active_guard",
        Integer,
        Computed("CASE WHEN status = 'active' THEN 1 ELSE NULL END"),
    ),
    UniqueConstraint(
        "user_id",
        "memory_type",
        "memory_key",
        "active_guard",
        name="idx_user_memory_active",
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

episodic_summaries = Table(
    "episodic_summaries",
    metadata,
    Column("summary_id", String(191), primary_key=True),
    Column("session_id", String(191), nullable=False),
    Column(
        "user_id",
        String(191),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("summary", LONGTEXT, nullable=False),
    Column(
        "resolved_entities",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'{}'" + ")"),
    ),
    Column("status", String(191), nullable=False, server_default=text("'active'")),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Column("expires_at", String(191), nullable=True),
    Index("idx_episodic_user", "user_id", "status", "updated_at"),
    UniqueConstraint("session_id", name="uq_episodic_summaries_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

memory_usage = Table(
    "memory_usage",
    metadata,
    Column("usage_id", String(191), primary_key=True),
    Column("memory_id", String(191), nullable=False),
    Column("session_id", String(191), nullable=False),
    Column("trace_id", String(191), nullable=True),
    Column("agent_id", String(191), nullable=False),
    Column("user_id", String(191), nullable=False),
    Column("created_at", String(191), nullable=False),
    Index("idx_memory_usage_trace", "trace_id", "session_id"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

memory_audit = Table(
    "memory_audit",
    metadata,
    Column("audit_id", String(191), primary_key=True),
    Column("memory_id", String(191), nullable=True),
    Column("user_id", String(191), nullable=False),
    Column("action", LONGTEXT, nullable=False),
    Column("actor_id", String(191), nullable=False),
    Column(
        "detail_json", LONGTEXT, nullable=False, server_default=text("(" + "'{}'" + ")")
    ),
    Column("created_at", String(191), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

memory_profile_revisions = Table(
    "memory_profile_revisions",
    metadata,
    Column(
        "user_id",
        String(191),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("profile_revision", Integer, nullable=False, server_default=text("0")),
    Column(
        "profile_prefix_hash",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "''" + ")"),
    ),
    Column("updated_at", String(191), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

users = Table(
    "users",
    metadata,
    Column("user_id", String(191), primary_key=True),
    Column("username", LONGTEXT, nullable=False),
    Column("username_normalized", String(191), nullable=False),
    Column("role", String(191), nullable=False, server_default=text("'user'")),
    Column("is_active", Integer, nullable=False, server_default=text("1")),
    Column("created_at", String(191), nullable=False),
    Column("last_login_at", LONGTEXT, nullable=True),
    UniqueConstraint("username_normalized", name="uq_users_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("token_hash", String(191), primary_key=True),
    Column(
        "user_id",
        String(191),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", String(191), nullable=False),
    Column("expires_at", String(191), nullable=False),
    Column("last_seen_at", LONGTEXT, nullable=False),
    Index("idx_auth_sessions_user", "user_id"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

uploads = Table(
    "uploads",
    metadata,
    Column("filename", String(191), primary_key=True),
    Column(
        "user_id",
        String(191),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("original_name", LONGTEXT, nullable=False),
    Column("content_type", LONGTEXT, nullable=False),
    Column("size", Integer, nullable=False),
    Column("created_at", String(191), nullable=False),
    Index("idx_uploads_user", "user_id", "created_at"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

conversation_feedback = Table(
    "conversation_feedback",
    metadata,
    Column("feedback_id", String(191), primary_key=True),
    Column(
        "assistant_message_id",
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("user_id", String(191), nullable=False),
    Column("rating", LONGTEXT, nullable=False),
    Column(
        "reason_codes",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column("comment", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Index("idx_feedback_message", "assistant_message_id"),
    UniqueConstraint(
        "assistant_message_id", "user_id", name="uq_conversation_feedback_2"
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

evaluation_jobs = Table(
    "evaluation_jobs",
    metadata,
    Column("job_id", String(191), primary_key=True),
    Column(
        "assistant_message_id",
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("user_id", String(191), nullable=False),
    Column("trigger_type", LONGTEXT, nullable=False),
    Column("feedback_version", BigInteger, nullable=False, server_default=text("0")),
    Column("status", String(191), nullable=False, server_default=text("'pending'")),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("scheduled_at", String(191), nullable=False),
    Column("lease_until", String(191), nullable=True),
    Column("last_error", LONGTEXT, nullable=True),
    Column("feedback_snapshot", LONGTEXT, nullable=True),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Index("idx_jobs_state", "status", "updated_at"),
    Index("idx_eval_jobs_status", "status", "scheduled_at"),
    UniqueConstraint(
        "assistant_message_id", "feedback_version", name="uq_evaluation_jobs_2"
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

conversation_evaluations = Table(
    "conversation_evaluations",
    metadata,
    Column("evaluation_id", String(191), primary_key=True),
    Column(
        "job_id",
        String(191),
        ForeignKey("evaluation_jobs.job_id", ondelete="NO ACTION"),
        nullable=False,
    ),
    Column(
        "assistant_message_id",
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("user_id", String(191), nullable=False),
    Column("overall_score", Float, nullable=False),
    Column("dimension_scores", LONGTEXT, nullable=False),
    Column("verdict", LONGTEXT, nullable=False),
    Column("safety_violation", Integer, nullable=False, server_default=text("0")),
    Column("attribution", LONGTEXT, nullable=False),
    Column(
        "rationale", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")
    ),
    Column(
        "recommendations",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column("extracted_experience", LONGTEXT, nullable=True),
    Column(
        "judge_model", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")
    ),
    Column(
        "rubric_version",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'v1'" + ")"),
    ),
    Column("is_superseded", Integer, nullable=False, server_default=text("0")),
    Column("created_at", String(191), nullable=False),
    Index("idx_evaluations_user", "user_id", "created_at"),
    UniqueConstraint("job_id", name="uq_conversation_evaluations_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

failure_cases = Table(
    "failure_cases",
    metadata,
    Column("failure_id", String(191), primary_key=True),
    Column(
        "evaluation_id",
        String(191),
        ForeignKey("conversation_evaluations.evaluation_id", ondelete="NO ACTION"),
        nullable=False,
    ),
    Column("user_id", String(191), nullable=False),
    Column("root_causes", LONGTEXT, nullable=False),
    Column(
        "evidence", LONGTEXT, nullable=False, server_default=text("(" + "'[]'" + ")")
    ),
    Column(
        "recommended_fix",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "''" + ")"),
    ),
    Column("status", String(191), nullable=False, server_default=text("'open'")),
    Column("created_at", String(191), nullable=False),
    UniqueConstraint("evaluation_id", name="uq_failure_cases_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

learned_experiences = Table(
    "learned_experiences",
    metadata,
    Column("experience_id", String(191), primary_key=True),
    Column("experience_type", LONGTEXT, nullable=False),
    Column("scope", String(191), nullable=False),
    Column("owner_user_id", String(191), nullable=True),
    Column("query_pattern", LONGTEXT, nullable=False),
    Column("content", LONGTEXT, nullable=False),
    Column("status", String(191), nullable=False, server_default=text("'candidate'")),
    Column("average_score", Float, nullable=False, server_default=text("0")),
    Column("support_count", Integer, nullable=False, server_default=text("1")),
    Column("conflict_count", Integer, nullable=False, server_default=text("0")),
    Column("version", Integer, nullable=False, server_default=text("1")),
    Column("supersedes_id", String(191), nullable=True),
    Column(
        "applicability",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column(
        "exclusions", LONGTEXT, nullable=False, server_default=text("(" + "'[]'" + ")")
    ),
    Column(
        "prerequisites",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column(
        "safety_notes", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")
    ),
    Column(
        "evidence_refs",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column(
        "risk_level", LONGTEXT, nullable=False, server_default=text("(" + "'low'" + ")")
    ),
    Column(
        "capability_tag",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "''" + ")"),
    ),
    Column("distinct_users", Integer, nullable=False, server_default=text("1")),
    Column("negative_count", Integer, nullable=False, server_default=text("0")),
    Column("expires_at", String(191), nullable=True),
    Column("last_validated_at", LONGTEXT, nullable=True),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Index("idx_experiences_active", "status", "scope", "owner_user_id"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

experience_sources = Table(
    "experience_sources",
    metadata,
    Column(
        "experience_id",
        String(191),
        ForeignKey("learned_experiences.experience_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "evaluation_id",
        String(191),
        ForeignKey("conversation_evaluations.evaluation_id", ondelete="NO ACTION"),
        primary_key=True,
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

experience_supports = Table(
    "experience_supports",
    metadata,
    Column(
        "experience_id",
        String(191),
        ForeignKey("learned_experiences.experience_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "assistant_message_id",
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "evaluation_id",
        String(191),
        ForeignKey("conversation_evaluations.evaluation_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("user_id", String(191), nullable=False),
    Column("score", Float, nullable=False),
    Column("created_at", String(191), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

experience_exposures = Table(
    "experience_exposures",
    metadata,
    Column(
        "experience_id",
        String(191),
        ForeignKey("learned_experiences.experience_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "assistant_message_id",
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("user_id", String(191), nullable=False),
    Column("bucket", String(191), nullable=False),
    Column("applied", Integer, nullable=False),
    Column("created_at", String(191), nullable=False),
    Index("idx_exposure_bucket", "experience_id", "bucket", "user_id"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

strategy_releases = Table(
    "strategy_releases",
    metadata,
    Column("release_id", String(191), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("active_ids", LONGTEXT, nullable=False),
    Column("previous_version", Integer, nullable=True),
    Column("action", LONGTEXT, nullable=False),
    Column("operator_user_id", String(191), nullable=False),
    Column("created_at", String(191), nullable=False),
    UniqueConstraint("version", name="uq_strategy_releases_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

session_deletion_audits = Table(
    "session_deletion_audits",
    metadata,
    Column("audit_id", String(191), primary_key=True),
    Column("session_id_hash", String(191), nullable=False),
    Column("user_id_hash", LONGTEXT, nullable=False),
    Column("deleted_message_count", Integer, nullable=False, server_default=text("0")),
    Column("deleted_feedback_count", Integer, nullable=False, server_default=text("0")),
    Column("deleted_job_count", Integer, nullable=False, server_default=text("0")),
    Column(
        "deleted_evaluation_count", Integer, nullable=False, server_default=text("0")
    ),
    Column("deleted_failure_count", Integer, nullable=False, server_default=text("0")),
    Column("deleted_trace_count", Integer, nullable=False, server_default=text("0")),
    Column(
        "affected_experience_ids",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column(
        "demoted_experience_ids",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column(
        "cleanup_status",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'pending'" + ")"),
    ),
    Column(
        "cleanup_errors",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column("created_at", String(191), nullable=False),
    Column("cleanup_completed_at", LONGTEXT, nullable=True),
    Index("idx_deletion_audits_created", "created_at"),
    UniqueConstraint("session_id_hash", name="uq_session_deletion_audits_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

traces = Table(
    "traces",
    metadata,
    Column("trace_id", String(191), primary_key=True),
    Column("session_id", String(191), nullable=False),
    Column("user_id", String(191), nullable=False, server_default=text("'default'")),
    Column("status", String(191), nullable=True, server_default=text("'ok'")),
    Column("start_time", String(191), nullable=False),
    Column("end_time", String(191), nullable=True),
    Column("duration_ms", Float, nullable=True),
    Column("mode", LONGTEXT, nullable=True, server_default=text("(" + "''" + ")")),
    Column("total_tokens", Integer, nullable=True, server_default=text("0")),
    Column("agents_involved", LONGTEXT, nullable=True),
    Column("span_count", Integer, nullable=True, server_default=text("0")),
    Column(
        "question_summary",
        LONGTEXT,
        nullable=True,
        server_default=text("(" + "''" + ")"),
    ),
    Column("tree_json", LONGTEXT, nullable=False),
    Column("created_at", String(191), nullable=False),
    Index("idx_trace_session", "session_id"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

spans = Table(
    "spans",
    metadata,
    Column("id", String(191), primary_key=True),
    Column(
        "trace_id",
        String(191),
        ForeignKey("traces.trace_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("parent_id", String(191), nullable=True),
    Column("span_type", String(191), nullable=False),
    Column("name", LONGTEXT, nullable=True, server_default=text("(" + "''" + ")")),
    Column("status", String(191), nullable=True, server_default=text("'ok'")),
    Column("start_time", String(191), nullable=False),
    Column("end_time", String(191), nullable=True),
    Column("duration_ms", Float, nullable=True),
    Column("error_message", LONGTEXT, nullable=True),
    Column("llm_attrs", LONGTEXT, nullable=True),
    Column("tool_attrs", LONGTEXT, nullable=True),
    Column("agent_attrs", LONGTEXT, nullable=True),
    Index("idx_span_parent", "parent_id"),
    Index("idx_span_type", "span_type"),
    Index("idx_trace_spans", "trace_id"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

knowledge_documents = Table(
    "knowledge_documents",
    metadata,
    Column("chunk_count", Integer, nullable=False, server_default=text("0")),
    Column("content", LONGTEXT, nullable=False, server_default=text("('')")),
    Column("version_id", String(191), primary_key=True),
    Column("document_id", String(191), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(191), nullable=False),
    Column("supersedes_version_id", String(191), nullable=True),
    Column("content_hash", LONGTEXT, nullable=False),
    Column("filename", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")),
    Column(
        "doc_type",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'general'" + ")"),
    ),
    Column("disease", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")),
    Column("source", LONGTEXT, nullable=False, server_default=text("(" + "''" + ")")),
    Column(
        "authority_level",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'user'" + ")"),
    ),
    Column("effective_at", String(191), nullable=True),
    Column("expires_at", String(191), nullable=True),
    Column("error", LONGTEXT, nullable=True),
    Column("created_at", String(191), nullable=False),
    Column("activated_at", LONGTEXT, nullable=True),
    Index("idx_knowledge_version_status", "status", "version_id"),
    Column(
        "active_guard",
        Integer,
        Computed("CASE WHEN status = 'active' THEN 1 ELSE NULL END"),
    ),
    UniqueConstraint(
        "document_id", "active_guard", name="idx_knowledge_active_document"
    ),
    UniqueConstraint("document_id", "version", name="uq_knowledge_documents_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

lifecycle_jobs = Table(
    "lifecycle_jobs",
    metadata,
    Column("job_id", String(191), primary_key=True),
    Column("job_type", LONGTEXT, nullable=False),
    Column("target_id", String(191), nullable=False, server_default=text("''")),
    Column("status", String(191), nullable=False),
    Column("result", LONGTEXT, nullable=False, server_default=text("(" + "'{}'" + ")")),
    Column("error", LONGTEXT, nullable=True),
    Column("actor_id", String(191), nullable=False),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

lifecycle_audit = Table(
    "lifecycle_audit",
    metadata,
    Column("audit_id", String(191), primary_key=True),
    Column("action", LONGTEXT, nullable=False),
    Column("actor_id", String(191), nullable=False),
    Column("target_id", String(191), nullable=False, server_default=text("''")),
    Column("result", LONGTEXT, nullable=False, server_default=text("(" + "'{}'" + ")")),
    Column("created_at", String(191), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

trusted_knowledge_sources = Table(
    "trusted_knowledge_sources",
    metadata,
    Column("version_id", String(191), primary_key=True),
    Column("document_id", String(191), nullable=False),
    Column("source_url", LONGTEXT, nullable=False),
    Column("verified_by", LONGTEXT, nullable=False),
    Column("verified_at", LONGTEXT, nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

knowledge_candidates = Table(
    "knowledge_candidates",
    metadata,
    Column("candidate_id", String(191), primary_key=True),
    Column("turn_id", String(191), nullable=False),
    Column("user_id", String(191), nullable=False),
    Column("claim_hash", String(191), nullable=False),
    Column("claim", LONGTEXT, nullable=False),
    Column("source_text", LONGTEXT, nullable=False),
    Column(
        "evidence_json",
        LONGTEXT,
        nullable=False,
        server_default=text("(" + "'[]'" + ")"),
    ),
    Column("status", String(191), nullable=False),
    Column("document_id", String(191), nullable=True),
    Column("reviewed_by", LONGTEXT, nullable=True),
    Column("error", LONGTEXT, nullable=True),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Column(
        "claim_guard",
        Integer,
        Computed("CASE WHEN status != 'rejected' THEN 1 ELSE NULL END"),
    ),
    UniqueConstraint(
        "claim_hash", "claim_guard", name="idx_knowledge_candidates_claim"
    ),
    Index("idx_knowledge_candidates_status", "status", "created_at"),
    UniqueConstraint("turn_id", "claim_hash", name="uq_knowledge_candidates_2"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

memory_extraction_runs = Table(
    "memory_extraction_runs",
    metadata,
    Column("turn_id", String(191), primary_key=True),
    Column("lane", String(191), primary_key=True),
    Column("status", String(191), nullable=False),
    Column("error", LONGTEXT, nullable=True),
    Column("updated_at", String(191), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

memory_lineage = Table(
    "memory_lineage",
    metadata,
    Column("lineage_id", String(191), primary_key=True),
    Column("owner_user_id", String(191), nullable=False),
    Column("memory_kind", String(191), nullable=False),
    Column("memory_key_hash", String(191), nullable=False),
    Column("source_type", String(191), nullable=False),
    Column("source_message_id", String(191), nullable=True),
    Column("source_trace_id", String(191), nullable=True),
    Column("source_document_id", String(191), nullable=True),
    Column("source_version_id", String(191), nullable=True),
    Column("source_chunk_uid", LONGTEXT, nullable=True),
    Column("valid_until", LONGTEXT, nullable=True),
    Column(
        "lineage_status", String(191), nullable=False, server_default=text("'valid'")
    ),
    Column("invalidated_reason", LONGTEXT, nullable=True),
    Column("created_at", String(191), nullable=False),
    Column("updated_at", String(191), nullable=False),
    Index("idx_memory_lineage_document", "source_document_id", "lineage_status"),
    UniqueConstraint(
        "owner_user_id", "memory_kind", "memory_key_hash", name="uq_memory_lineage_2"
    ),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)


jobs = Table(
    "jobs",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("dedup_key", String(191), nullable=False, unique=True),
    Column("payload", JSON, nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("token", BigInteger, nullable=False, server_default="0"),
    Column("owner", String(64)),
    Column("scheduled_at", DateTime(fsp=6)),
    Column("lease_until", DateTime(fsp=6)),
    Column("error", LONGTEXT),
    Column("created_at", DateTime(fsp=6), nullable=False),
    Index("idx_job_claim", "kind", "status", "scheduled_at", "lease_until"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

runs = Table(
    "chat_runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("session_id", String(191), nullable=False),
    Column("user_id", String(191), nullable=False),
    Column("request", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("hitl", Integer, nullable=False),
    Column("idempotency_key", String(191)),
    Column("request_hash", String(64), nullable=False),
    Column("result", JSON),
    Column("error", LONGTEXT),
    Column("seq", BigInteger, nullable=False, server_default="0"),
    Column("elapsed_seconds", Float, nullable=False, server_default="0"),
    Column("created_at", DateTime(fsp=6), nullable=False),
    Column("updated_at", DateTime(fsp=6), nullable=False),
    Column(
        "active_session",
        String(191),
        Computed(
            "CASE WHEN status IN ('queued','running','waiting_answer') "
            "THEN session_id ELSE NULL END"
        ),
    ),
    UniqueConstraint("active_session", name="uq_run_active_session"),
    UniqueConstraint("user_id", "idempotency_key", name="uq_run_idempotency"),
    Index("idx_run_user_status", "user_id", "status"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

run_events = Table(
    "run_events",
    metadata,
    Column(
        "run_id",
        String(64),
        ForeignKey("chat_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("seq", BigInteger, primary_key=True),
    Column("event", String(64), nullable=False),
    Column("data", JSON, nullable=False),
    Column("created_at", DateTime(fsp=6), nullable=False),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

questionnaires = Table(
    "questionnaires",
    metadata,
    Column("questionnaire_id", String(64), primary_key=True),
    Column(
        "run_id",
        String(64),
        ForeignKey("chat_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("payload", JSON, nullable=False),
    Column("answers", JSON),
    Column("status", String(16), nullable=False),
    Column("expires_at", DateTime(fsp=6), nullable=False),
    Index("idx_questionnaire_run", "run_id", "status"),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

admission = Table(
    "admission",
    metadata,
    Column("name", String(32), primary_key=True),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)

slots = Table(
    "capacity_slots",
    metadata,
    Column("slot_id", String(64), primary_key=True),
    Column("resource", String(32), nullable=False),
    Column("owner", String(64)),
    Column("background", Integer, nullable=False, server_default="0"),
    Column("token", BigInteger, nullable=False, server_default="0"),
    Column("lease_until", DateTime(fsp=6)),
    mysql_engine="InnoDB",
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_bin",
)
