"""知识版本、引用安全和记忆血缘测试。"""

from pathlib import Path
from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import AsyncMock

import pytest

from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.api.services import knowledge_service
from mediZJ.memory.lifecycle import DataLifecycleService
from mediZJ.memory import lifecycle as lifecycle_module
from mediZJ.memory.lineage import MemoryLineageStore
from mediZJ.validation.medical_answer import (
    CitationValidator,
    MedicalAnswerVerifier,
)


@pytest.fixture
def catalog(tmp_path: Path) -> KnowledgeCatalog:
    KnowledgeCatalog.reset()
    return KnowledgeCatalog(tmp_path / "catalog.db")


def _metadata(name: str = "guide.txt") -> dict:
    return {
        "filename": name,
        "type": "clinical_guideline",
        "disease": "高血压",
        "source": "临床指南数据库",
        "authority_level": "authoritative",
    }


def test_version_switch_is_atomic_and_duplicate_is_rejected(catalog):
    first = catalog.begin_version("hypertension", "hash-1", _metadata())
    first = catalog.activate(first["version_id"])
    second = catalog.begin_version("hypertension", "hash-2", _metadata())

    assert catalog.active_version("hypertension")["version_id"] == first["version_id"]
    second = catalog.activate(second["version_id"])
    assert catalog.active_version("hypertension")["version_id"] == second["version_id"]
    versions = catalog.list_versions("hypertension")
    assert [item["status"] for item in versions] == ["active", "archived"]

    with pytest.raises(ValueError, match="内容相同"):
        catalog.begin_version("another", "hash-2", _metadata("another.txt"))


def test_failed_version_does_not_replace_active(catalog):
    active = catalog.begin_version("doc", "hash-1", _metadata())
    catalog.activate(active["version_id"])
    failed = catalog.begin_version("doc", "hash-2", _metadata())
    catalog.mark_failed(failed["version_id"], "embedding failed")

    assert catalog.active_version("doc")["version_id"] == active["version_id"]
    assert catalog.get_version(failed["version_id"])["status"] == "failed"
    assert [item["version_id"] for item in catalog.list_versions("doc")] == [
        active["version_id"]
    ]


def test_future_effective_version_is_not_active_for_retrieval(catalog):
    metadata = _metadata()
    metadata["effective_at"] = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).isoformat()
    future = catalog.begin_version("future", "hash-future", metadata)
    catalog.activate(future["version_id"])
    assert catalog.active_version("future") is None
    assert catalog.active_by_version(future["version_id"]) is None


def test_citation_validator_rejects_archived_and_enriches_active(catalog):
    active = catalog.begin_version("doc", "hash-1", _metadata())
    active = catalog.activate(active["version_id"])
    validator = CitationValidator(catalog=catalog, knowledge_base=None)

    valid = validator.validate([{"index": 9, "doc_id": "doc"}])
    assert valid[0]["version_id"] == active["version_id"]
    assert valid[0]["validation_status"] == "valid"

    replacement = catalog.begin_version("doc", "hash-2", _metadata())
    catalog.activate(replacement["version_id"])
    assert validator.validate([{
        "index": 1,
        "doc_id": "doc",
        "version_id": active["version_id"],
    }]) == []


@pytest.mark.asyncio
async def test_verifier_blocks_risky_diagnosis_without_care_advice(catalog):
    verifier = MedicalAnswerVerifier(
        citation_validator=CitationValidator(catalog=catalog),
        llm_client=None,
    )
    result = await verifier.verify(
        "我胸痛且呼吸困难",
        "您肯定是冠心病。",
        [],
    )

    assert result.passed is False
    assert "高风险症状未明确建议就医" in result.violations
    assert "存在越界的确定性诊断" in result.violations


@pytest.mark.asyncio
async def test_verifier_detects_prescription_and_unsupported_number(catalog):
    verifier = MedicalAnswerVerifier(
        citation_validator=CitationValidator(catalog=catalog),
        llm_client=None,
    )
    result = await verifier.verify(
        "头痛怎么办",
        "建议服用布洛芬 200mg，有效率为 90%。",
        [],
    )

    assert "存在未经医生评估的具体处方建议" in result.violations
    assert "数值性医学主张缺少有效来源" in result.violations


@pytest.mark.asyncio
async def test_semantic_verifier_and_single_rewrite(catalog):
    class FakeLlm:
        def __init__(self):
            self.calls = 0

        async def chat(self, _messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return (
                    '{"passed":false,"violations":["把用户自述当成确诊"],'
                    '"completeness":3,"personalization":3,"clarity":4}'
                )
            if self.calls == 2:
                return "仅能说明您报告了相关症状，尚不能确诊，请咨询医生。"
            return (
                '{"passed":true,"violations":[],"completeness":4,'
                '"personalization":4,"clarity":4}'
            )

    verifier = MedicalAnswerVerifier(
        citation_validator=CitationValidator(catalog=catalog),
        llm_client=FakeLlm(),
    )
    answer, result = await verifier.verify_and_rewrite(
        "我觉得自己有抑郁症",
        "您已经有抑郁症。",
        [],
    )

    assert result.passed
    assert "尚不能确诊" in answer


def test_citation_validator_checks_chunk_uid(catalog):
    version = catalog.begin_version("doc", "hash-chunk", _metadata())
    version = catalog.activate(version["version_id"])

    class FakeKnowledgeBase:
        def get_document_chunks(self, _version_id):
            return [{"metadata": {"chunk_uid": f"{version['version_id']}:0"}}]

    validator = CitationValidator(
        catalog=catalog,
        knowledge_base=FakeKnowledgeBase(),
    )
    assert validator.validate([{
        "doc_id": "doc",
        "version_id": version["version_id"],
        "chunk_uid": f"{version['version_id']}:0",
    }])
    assert validator.validate([{
        "doc_id": "doc",
        "version_id": version["version_id"],
        "chunk_uid": "forged:0",
    }]) == []


def test_memory_lineage_can_be_invalidated_without_deleting(tmp_path):
    store = MemoryLineageStore(tmp_path / "sessions.db")
    store.record(
        "user-1",
        "summary",
        "memory-key",
        "authoritative_document",
        source_document_id="doc-1",
    )
    assert store.is_valid("user-1", "summary", "memory-key")
    assert store.invalidate_document("doc-1", "document_archived") == 1
    assert not store.is_valid("user-1", "summary", "memory-key")
    assert store.delete_user("user-1") == 1


def test_memory_lineage_validates_source_and_expiry(tmp_path):
    store = MemoryLineageStore(tmp_path / "sessions.db")
    with pytest.raises(ValueError, match="来源类型"):
        store.record("u1", "profile", "key", "unknown")
    store.record(
        "u1",
        "profile",
        "expired",
        "user_reported",
        valid_until="2020-01-01T00:00:00+00:00",
    )
    assert not store.is_valid("u1", "profile", "expired")
    assert store.is_valid("u1", "profile", "unregistered")


def test_catalog_jobs_and_schema_migration(catalog):
    job_id = catalog.create_job("prune_expired", "", "admin")
    catalog.finish_job(job_id, "completed", {"memory_usage": 1})
    assert catalog.get_job(job_id)["result"]["memory_usage"] == 1
    catalog.audit("prune_expired", "admin", "", {"count": 1})
    with sqlite3.connect(catalog.db_path) as conn:
        assert conn.execute(
            "SELECT value FROM knowledge_schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "3"
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'knowledge_conflicts'"
        ).fetchone() is None


def test_activation_keeps_only_active_and_previous(catalog):
    first = catalog.begin_version("doc", "hash-1", _metadata())
    catalog.activate(first["version_id"])
    second = catalog.begin_version("doc", "hash-2", _metadata())
    catalog.activate(second["version_id"])
    third = catalog.begin_version("doc", "hash-3", _metadata())

    class FakeKnowledgeBase:
        def __init__(self):
            self.deleted = []

        def delete_document(self, version_id):
            self.deleted.append(version_id)
            return 1

    knowledge_base = FakeKnowledgeBase()
    knowledge_service._prune_versions_before_activation(
        knowledge_base,
        catalog,
        "doc",
        third["version_id"],
    )
    catalog.activate(third["version_id"])

    versions = catalog.list_versions("doc")
    assert [item["version_id"] for item in versions] == [
        third["version_id"],
        second["version_id"],
    ]
    assert knowledge_base.deleted == [first["version_id"]]


def test_ingest_cleanup_failure_preserves_active(catalog, monkeypatch):
    first = catalog.begin_version("doc", "hash-1", _metadata())
    catalog.activate(first["version_id"])
    second = catalog.begin_version("doc", "hash-2", _metadata())
    catalog.activate(second["version_id"])

    class FakeKnowledgeBase:
        def __init__(self):
            self.pending_id = ""
            self.deleted = []

        def add_documents(self, documents):
            self.pending_id = documents[0]["id"]
            return 1

        def delete_document(self, version_id):
            self.deleted.append(version_id)
            if version_id == first["version_id"]:
                raise RuntimeError("cleanup failed")
            return 1

    knowledge_base = FakeKnowledgeBase()
    monkeypatch.setattr(
        knowledge_service,
        "MedicalKnowledgeBase",
        lambda: knowledge_base,
    )
    monkeypatch.setattr(
        knowledge_service,
        "_catalog_with_legacy",
        lambda _kb: catalog,
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        knowledge_service._ingest_version(
            "doc",
            "new content",
            {**_metadata(), "content_hash": "hash-3"},
        )

    assert catalog.active_version("doc")["version_id"] == second["version_id"]
    assert catalog.get_version(knowledge_base.pending_id)["status"] == "failed"
    assert knowledge_base.deleted[-1] == knowledge_base.pending_id


def test_document_delete_removes_all_versions(catalog, monkeypatch):
    first = catalog.begin_version("doc", "hash-1", _metadata())
    catalog.activate(first["version_id"])
    second = catalog.begin_version("doc", "hash-2", _metadata())
    catalog.activate(second["version_id"])

    class FakeKnowledgeBase:
        def __init__(self):
            self.deleted = []

        def delete_document(self, version_id):
            self.deleted.append(version_id)
            return 2

    knowledge_base = FakeKnowledgeBase()
    monkeypatch.setattr(
        knowledge_service,
        "MedicalKnowledgeBase",
        lambda: knowledge_base,
    )
    monkeypatch.setattr(
        knowledge_service,
        "_catalog_with_legacy",
        lambda _kb: catalog,
    )
    monkeypatch.setattr(
        "mediZJ.memory.lineage.MemoryLineageStore.invalidate_document",
        lambda _self, _document_id, _reason: 0,
    )

    result = knowledge_service.delete_document("doc")

    assert result.chunks_deleted == 4
    assert catalog.document_versions("doc") == []
    assert set(knowledge_base.deleted) == {
        first["version_id"],
        second["version_id"],
    }


@pytest.mark.asyncio
async def test_lifecycle_prunes_memory_only():
    class FakeCatalog:
        def __init__(self):
            self.finished = None

        def create_job(self, *_args):
            return "job-1"

        def finish_job(self, *args):
            self.finished = args

        def audit(self, *_args):
            return None

        def get_job(self, _job_id):
            return {"job_id": "job-1", "status": "completed"}

    fake_catalog = FakeCatalog()
    service = DataLifecycleService(catalog=fake_catalog)
    service._prune_memory_rows = lambda: {"memory_usage": 2}
    job = await service.prune_expired("admin")

    assert job["status"] == "completed"
    assert fake_catalog.finished[1] == "completed"
    assert fake_catalog.finished[2] == {"memory_usage": 2}


@pytest.mark.asyncio
async def test_lifecycle_deletes_user_data_and_records_job(tmp_path, monkeypatch):
    database = tmp_path / "sessions.db"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (session_id TEXT PRIMARY KEY, user_id TEXT);
            CREATE TABLE profiles (user_id TEXT PRIMARY KEY);
            CREATE TABLE auth_sessions (token TEXT PRIMARY KEY, user_id TEXT);
            CREATE TABLE traces (trace_id TEXT PRIMARY KEY, user_id TEXT);
            INSERT INTO sessions VALUES ('s1', 'u1');
            INSERT INTO profiles VALUES ('u1');
            INSERT INTO auth_sessions VALUES ('t1', 'u1');
            INSERT INTO traces VALUES ('tr1', 'u1');
            """
        )

    class FakeSessionDb:
        db_path = str(database)

        def list_sessions(self, **_kwargs):
            return [{"session_id": "s1"}]

    class FakeCatalog:
        def __init__(self):
            self.job = None

        def create_job(self, *_args):
            return "delete-job"

        def finish_job(self, job_id, status, result, error=None):
            self.job = {
                "job_id": job_id,
                "status": status,
                "result": result,
                "error": error,
            }

        def audit(self, *_args):
            return None

        def get_job(self, _job_id):
            return self.job

    class FakeVectors:
        def delete_session(self, _session_id):
            return None

    monkeypatch.setattr(lifecycle_module, "SessionVectorStore", FakeVectors)
    catalog = FakeCatalog()
    service = DataLifecycleService(
        catalog=catalog,
        session_db=FakeSessionDb(),
    )
    job = await service.delete_user("u1", "admin")

    assert job["status"] == "completed"
    assert job["result"]["sessions"] == 1
    assert job["result"]["profiles"] == 1
    assert job["result"]["traces"] == 1


@pytest.mark.asyncio
async def test_lifecycle_retry_routes_failed_job(monkeypatch):
    class FakeCatalog:
        def get_job(self, _job_id):
            return {
                "status": "failed",
                "job_type": "delete_user",
                "target_id": "u1",
            }

    service = DataLifecycleService(catalog=FakeCatalog())
    service.delete_user = AsyncMock(return_value={"status": "completed"})
    assert (await service.retry("job", "admin"))["status"] == "completed"

    service.catalog.get_job = lambda _job_id: None
    with pytest.raises(LookupError, match="作业不存在"):
        await service.retry("missing", "admin")


@pytest.mark.asyncio
async def test_lifecycle_worker_start_stop_and_disabled(monkeypatch):
    lifecycle_module._lifecycle_task = None
    lifecycle_module._lifecycle_stop = None
    monkeypatch.setenv("DATA_LIFECYCLE_ENABLED", "false")
    await lifecycle_module.start_lifecycle_worker()
    assert lifecycle_module._lifecycle_task is None

    monkeypatch.setenv("DATA_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("DATA_LIFECYCLE_INTERVAL_SECONDS", "3600")
    await lifecycle_module.start_lifecycle_worker()
    task = lifecycle_module._lifecycle_task
    assert task is not None
    await lifecycle_module.start_lifecycle_worker()
    assert lifecycle_module._lifecycle_task is task
    await lifecycle_module.stop_lifecycle_worker()
    assert lifecycle_module._lifecycle_task is None
    await lifecycle_module.stop_lifecycle_worker()


def test_lifecycle_deletes_summary_files(tmp_path, monkeypatch):
    summary = tmp_path / "summary-session-1.json"
    unrelated = tmp_path / "summary-other.json"
    summary.write_text("{}", encoding="utf-8")
    unrelated.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lifecycle_module, "DEFAULT_SESSION_SUMMARY_DIR", tmp_path)

    DataLifecycleService._delete_summary_files("session-1")

    assert not summary.exists()
    assert unrelated.exists()


def _expired_metadata() -> dict:
    meta = _metadata()
    meta["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    return meta


def test_expire_documents_marks_expired(catalog):
    version = catalog.begin_version("doc", "hash-1", _expired_metadata())
    catalog.activate(version["version_id"])

    assert catalog.expire_documents(datetime.now(timezone.utc).isoformat()) == 1
    assert catalog.active_version("doc") is None
    assert catalog.get_version(version["version_id"])["status"] == "expired"

    visible = catalog.list_active_and_expired()
    assert any(
        item["version_id"] == version["version_id"] and item["status"] == "expired"
        for item in visible
    )


def test_version_chain_when_active_expired(catalog):
    v1 = catalog.begin_version("doc", "hash-1", _metadata())
    catalog.activate(v1["version_id"])
    v2 = catalog.begin_version("doc", "hash-2", _expired_metadata())
    catalog.activate(v2["version_id"])

    catalog.expire_documents(datetime.now(timezone.utc).isoformat())
    assert catalog.get_version(v2["version_id"])["status"] == "expired"

    # 过期后上传新版，应继承过期版作为前任
    v3 = catalog.begin_version("doc", "hash-3", _metadata())
    assert v3["supersedes_version_id"] == v2["version_id"]

    # 激活新版后，过期版转 archived
    catalog.activate(v3["version_id"])
    assert catalog.get_version(v2["version_id"])["status"] == "archived"
    assert catalog.get_version(v3["version_id"])["status"] == "active"


def test_citation_validator_rejects_expired(catalog):
    version = catalog.begin_version("doc", "hash-1", _expired_metadata())
    catalog.activate(version["version_id"])
    catalog.expire_documents(datetime.now(timezone.utc).isoformat())

    validator = CitationValidator(catalog=catalog, knowledge_base=None)
    assert validator.validate([{
        "index": 1,
        "doc_id": "doc",
        "version_id": version["version_id"],
    }]) == []


def test_schema_migration_v2_to_v3_preserves_data(tmp_path):
    db = tmp_path / "catalog.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE knowledge_schema_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            INSERT INTO knowledge_schema_meta VALUES ('schema_version', '2');
            CREATE TABLE knowledge_documents (
                version_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('indexing', 'active', 'archived', 'failed')
                ),
                supersedes_version_id TEXT,
                content_hash TEXT NOT NULL,
                filename TEXT NOT NULL DEFAULT '',
                doc_type TEXT NOT NULL DEFAULT 'general',
                disease TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                authority_level TEXT NOT NULL DEFAULT 'user',
                effective_at TEXT,
                expires_at TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                UNIQUE(document_id, version)
            );
            INSERT INTO knowledge_documents
                (version_id, document_id, version, status, content_hash,
                 filename, created_at)
                VALUES ('kv_old', 'doc', 1, 'active', 'hash', 'a.txt',
                        '2024-01-01T00:00:00+00:00');
            """
        )

    KnowledgeCatalog.reset()
    catalog = KnowledgeCatalog(db)

    row = catalog.get_version("kv_old")
    assert row["document_id"] == "doc"
    assert row["status"] == "active"

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT value FROM knowledge_schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "3"
        # 迁移后的 CHECK 应允许 expired 状态
        conn.execute(
            """
            INSERT INTO knowledge_documents
                (version_id, document_id, version, status, content_hash,
                 filename, created_at)
                VALUES ('kv_expired', 'doc2', 1, 'expired', 'hash2', 'b.txt',
                        '2024-01-01T00:00:00+00:00')
            """
        )
        conn.commit()
