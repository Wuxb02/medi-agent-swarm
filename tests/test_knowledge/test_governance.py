"""知识版本、引用安全和记忆血缘测试。"""

from pathlib import Path
from datetime import datetime, timezone
import sqlite3
from unittest.mock import AsyncMock

import pytest

from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.knowledge.conflict_detector import MedicalConflictDetector
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


def test_citation_validator_rejects_archived_and_enriches_active(catalog):
    active = catalog.begin_version("doc", "hash-1", _metadata())
    active = catalog.activate(active["version_id"])
    validator = CitationValidator(catalog=catalog, knowledge_base=None)

    valid = validator.validate([{"index": 9, "doc_id": "doc"}])
    assert valid[0]["version_id"] == active["version_id"]
    assert valid[0]["validation_status"] == "valid"

    catalog.archive_document("doc")
    assert validator.validate([{"index": 1, "doc_id": "doc"}]) == []


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


def test_conflict_requires_explicit_admin_review(catalog):
    conflict = {
        "conflict_id": "conflict-1",
        "left_version_id": "left",
        "left_chunk_uid": "left:0",
        "right_version_id": "right",
        "right_chunk_uid": "right:0",
        "conflict_type": "threshold_difference",
        "confidence": 0.8,
        "explanation": "两条血压阈值不一致",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    catalog.upsert_conflict(conflict)

    assert catalog.list_conflicts("pending")[0]["conflict_id"] == "conflict-1"
    assert catalog.review_conflict("conflict-1", "confirmed", "admin")
    reviewed = catalog.list_conflicts("confirmed")[0]
    assert reviewed["reviewer"] == "admin"

    with pytest.raises(ValueError, match="非法冲突"):
        catalog.review_conflict("conflict-1", "active", "admin")


def test_catalog_jobs_and_archived_cleanup_candidates(catalog):
    version = catalog.begin_version("doc", "hash", _metadata())
    catalog.activate(version["version_id"])
    catalog.archive_document("doc")

    assert catalog.archived_before("9999-01-01T00:00:00+00:00")
    job_id = catalog.create_job("prune_expired", "", "admin")
    catalog.finish_job(job_id, "completed", {"knowledge_versions": 1})
    assert catalog.get_job(job_id)["result"]["knowledge_versions"] == 1
    catalog.audit("prune_expired", "admin", "", {"count": 1})
    assert catalog.delete_version_record(version["version_id"])
    assert not catalog.delete_version_record("missing")


@pytest.mark.asyncio
async def test_conflict_detector_creates_review_candidate():
    left_version = {
        "version_id": "left",
        "disease": "高血压",
        "doc_type": "clinical_guideline",
    }
    right_version = {
        "version_id": "right",
        "disease": "高血压",
        "doc_type": "clinical_guideline",
    }

    class FakeCatalog:
        def __init__(self):
            self.saved = []

        def get_version(self, version_id):
            return left_version if version_id == "left" else None

        def active_by_version(self, version_id):
            return right_version if version_id == "right" else None

        def upsert_conflict(self, conflict):
            self.saved.append(conflict)

    class FakeKnowledgeBase:
        def get_document_chunks(self, version_id):
            if version_id == "left":
                return [{
                    "content": "目标血压低于 130 mmHg",
                    "metadata": {"chunk_uid": "left:0"},
                }]
            return [{
                "content": "目标血压低于 140 mmHg",
                "metadata": {"chunk_uid": "right:0"},
            }]

        def search(self, _query, top_k):
            assert top_k == 12
            return [{
                "score": 0.9,
                "metadata": {"version_id": "right"},
            }]

    catalog = FakeCatalog()
    detector = MedicalConflictDetector(
        catalog=catalog,
        knowledge_base=FakeKnowledgeBase(),
    )
    conflicts = await detector.detect_version("left")

    assert conflicts[0]["review_status"] == "pending"
    assert conflicts[0]["left_chunk_uid"] == "left:0"
    assert catalog.saved == conflicts


@pytest.mark.asyncio
async def test_conflict_detector_records_failure():
    class BrokenCatalog:
        def __init__(self):
            self.saved = []

        def get_version(self, _version_id):
            return {"version_id": "left"}

        def upsert_conflict(self, conflict):
            self.saved.append(conflict)

    class BrokenKnowledgeBase:
        def get_document_chunks(self, _version_id):
            raise RuntimeError("vector unavailable")

    catalog = BrokenCatalog()
    detector = MedicalConflictDetector(
        catalog=catalog,
        knowledge_base=BrokenKnowledgeBase(),
    )
    conflicts = await detector.detect_version("left")

    assert conflicts[0]["detection_status"] == "failed"
    assert "vector unavailable" in conflicts[0]["error"]


@pytest.mark.asyncio
async def test_lifecycle_prunes_archived_versions(monkeypatch):
    class FakeCatalog:
        def __init__(self):
            self.finished = None

        def create_job(self, *_args):
            return "job-1"

        def archived_before(self, _cutoff):
            return [{"version_id": "old"}]

        def delete_version_record(self, version_id):
            return version_id == "old"

        def finish_job(self, *args):
            self.finished = args

        def audit(self, *_args):
            return None

        def get_job(self, _job_id):
            return {"job_id": "job-1", "status": "completed"}

    class FakeKnowledgeBase:
        def __init__(self):
            self.deleted = []

        def delete_document(self, version_id):
            self.deleted.append(version_id)

    fake_catalog = FakeCatalog()
    fake_kb = FakeKnowledgeBase()
    monkeypatch.setattr(
        "mediZJ.memory.lifecycle.MedicalKnowledgeBase", lambda: fake_kb
    )
    service = DataLifecycleService(catalog=fake_catalog)
    job = await service.prune_expired("admin")

    assert job["status"] == "completed"
    assert fake_kb.deleted == ["old"]
    assert fake_catalog.finished[1] == "completed"


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

    class DisabledMemory:
        enabled = False

        def __init__(self, user_id):
            self.user_id = user_id

    monkeypatch.setattr(lifecycle_module, "SessionVectorStore", FakeVectors)
    monkeypatch.setattr(lifecycle_module, "LongTermMemory", DisabledMemory)
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


@pytest.mark.asyncio
async def test_lifecycle_mem0_success_and_failure(monkeypatch):
    class FakeMem0:
        def __init__(self, fails=False):
            self.fails = fails

        def delete_all(self, **_kwargs):
            if self.fails:
                raise RuntimeError("external unavailable")

    class EnabledMemory:
        enabled = True
        fails = False

        def __init__(self, user_id):
            self.user_id = user_id
            self.mem0 = FakeMem0(self.fails)

    monkeypatch.setattr(lifecycle_module, "LongTermMemory", EnabledMemory)
    assert await DataLifecycleService._delete_mem0("u1") is None
    EnabledMemory.fails = True
    assert "external unavailable" in (
        await DataLifecycleService._delete_mem0("u1")
    )


def test_lifecycle_deletes_summary_files(tmp_path, monkeypatch):
    summary = tmp_path / "summary-session-1.json"
    unrelated = tmp_path / "summary-other.json"
    summary.write_text("{}", encoding="utf-8")
    unrelated.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lifecycle_module, "DEFAULT_SESSION_SUMMARY_DIR", tmp_path)

    DataLifecycleService._delete_summary_files("session-1")

    assert not summary.exists()
    assert unrelated.exists()
