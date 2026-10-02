"""知识版本、引用安全和记忆血缘测试。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
from mediZJ.infrastructure.database import transaction

from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.api.services import knowledge_service
from mediZJ.memory.lifecycle import DataLifecycleService
from mediZJ.memory.lineage import MemoryLineageStore
from mediZJ.validation.medical_answer import CitationValidator, MedicalAnswerVerifier

pytestmark = [
    pytest.mark.integration,
    pytest.mark.infrastructure,
    pytest.mark.usefixtures("mysql_infrastructure"),
]


@pytest.fixture
def catalog(mysql_infrastructure) -> KnowledgeCatalog:
    KnowledgeCatalog.reset()
    return KnowledgeCatalog()


def _metadata(name: str = "guide.txt") -> dict:
    return {
        "filename": name,
        "type": "clinical_guideline",
        "disease": "高血压",
        "source": "临床指南数据库",
        "authority_level": "authoritative",
    }


async def test_version_switch_is_atomic_and_duplicate_is_rejected(catalog):
    first = await catalog.begin_version("hypertension", "hash-1", _metadata())
    first = await catalog.activate(first["version_id"])
    second = await catalog.begin_version("hypertension", "hash-2", _metadata())
    assert (await catalog.active_version("hypertension"))["version_id"] == first[
        "version_id"
    ]
    second = await catalog.activate(second["version_id"])
    assert (await catalog.active_version("hypertension"))["version_id"] == second[
        "version_id"
    ]
    versions = await catalog.list_versions("hypertension")
    assert [item["status"] for item in versions] == ["active", "archived"]
    with pytest.raises(ValueError, match="内容相同"):
        await catalog.begin_version("another", "hash-2", _metadata("another.txt"))


async def test_failed_version_does_not_replace_active(catalog):
    active = await catalog.begin_version("doc", "hash-1", _metadata())
    await catalog.activate(active["version_id"])
    failed = await catalog.begin_version("doc", "hash-2", _metadata())
    await catalog.mark_failed(failed["version_id"], "embedding failed")
    assert (await catalog.active_version("doc"))["version_id"] == active["version_id"]
    assert (await catalog.get_version(failed["version_id"]))["status"] == "failed"
    assert [item["version_id"] for item in await catalog.list_versions("doc")] == [
        active["version_id"]
    ]


async def test_future_effective_version_is_not_active_for_retrieval(catalog):
    metadata = _metadata()
    metadata["effective_at"] = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).isoformat()
    future = await catalog.begin_version("future", "hash-future", metadata)
    await catalog.activate(future["version_id"])
    assert await catalog.active_version("future") is None
    assert await catalog.active_by_version(future["version_id"]) is None


async def test_citation_validator_rejects_archived_and_enriches_active(catalog):
    active = await catalog.begin_version("doc", "hash-1", _metadata())
    active = await catalog.activate(active["version_id"])
    validator = CitationValidator(catalog=catalog, knowledge_base=None)
    valid = await validator.validate([{"index": 9, "doc_id": "doc"}])
    assert valid[0]["version_id"] == active["version_id"]
    assert valid[0]["validation_status"] == "valid"
    replacement = await catalog.begin_version("doc", "hash-2", _metadata())
    await catalog.activate(replacement["version_id"])
    assert (
        await validator.validate(
            [{"index": 1, "doc_id": "doc", "version_id": active["version_id"]}]
        )
        == []
    )


async def test_citation_validator_keeps_body_reference_index(catalog):
    version = await catalog.begin_version("doc", "hash-index", _metadata())
    await catalog.activate(version["version_id"])
    validator = CitationValidator(catalog=catalog)
    valid = await validator.validate(
        [{"index": 1, "doc_id": "missing"}, {"index": 4, "doc_id": "doc"}]
    )
    assert [item["index"] for item in valid] == [4]


@pytest.mark.asyncio
async def test_verifier_blocks_risky_diagnosis_without_care_advice(catalog):
    verifier = MedicalAnswerVerifier(
        citation_validator=CitationValidator(catalog=catalog), llm_client=None
    )
    result = await verifier.verify("我胸痛且呼吸困难", "您肯定是冠心病。", [])
    assert result.passed is False
    assert "高风险症状未明确建议就医" in result.violations
    assert "存在越界的确定性诊断" in result.violations


@pytest.mark.asyncio
async def test_verifier_detects_prescription_and_unsupported_number(catalog):
    verifier = MedicalAnswerVerifier(
        citation_validator=CitationValidator(catalog=catalog), llm_client=None
    )
    result = await verifier.verify(
        "头痛怎么办", "建议服用布洛芬 200mg，有效率为 90%。", []
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
                return '{"passed":false,"violations":["把用户自述当成确诊"],"completeness":3,"personalization":3,"clarity":4}'
            if self.calls == 2:
                return "仅能说明您报告了相关症状，尚不能确诊，请咨询医生。"
            return '{"passed":true,"violations":[],"completeness":4,"personalization":4,"clarity":4}'

    verifier = MedicalAnswerVerifier(
        citation_validator=CitationValidator(catalog=catalog), llm_client=FakeLlm()
    )
    (answer, result) = await verifier.verify_and_rewrite(
        "我觉得自己有抑郁症", "您已经有抑郁症。", []
    )
    assert result.passed
    assert "尚不能确诊" in answer


async def test_citation_validator_checks_chunk_uid(catalog):
    version = await catalog.begin_version("doc", "hash-chunk", _metadata())
    version = await catalog.activate(version["version_id"])

    class FakeKnowledgeBase:
        def get_document_chunks(self, _version_id):
            return [{"metadata": {"chunk_uid": f"{version['version_id']}:0"}}]

    validator = CitationValidator(catalog=catalog, knowledge_base=FakeKnowledgeBase())
    assert await validator.validate(
        [
            {
                "doc_id": "doc",
                "version_id": version["version_id"],
                "chunk_uid": f"{version['version_id']}:0",
            }
        ]
    )
    assert (
        await validator.validate(
            [
                {
                    "doc_id": "doc",
                    "version_id": version["version_id"],
                    "chunk_uid": "forged:0",
                }
            ]
        )
        == []
    )


async def test_memory_lineage_can_be_invalidated_without_deleting(tmp_path):
    store = MemoryLineageStore()
    await store.record(
        "user-1",
        "summary",
        "memory-key",
        "authoritative_document",
        source_document_id="doc-1",
    )
    assert await store.is_valid("user-1", "summary", "memory-key")
    assert await store.invalidate_document("doc-1", "document_archived") == 1
    assert not await store.is_valid("user-1", "summary", "memory-key")
    assert await store.delete_user("user-1") == 1


async def test_memory_lineage_validates_source_and_expiry(tmp_path):
    store = MemoryLineageStore()
    with pytest.raises(ValueError, match="来源类型"):
        await store.record("u1", "profile", "key", "unknown")
    await store.record(
        "u1",
        "profile",
        "expired",
        "user_reported",
        valid_until="2020-01-01T00:00:00+00:00",
    )
    assert not await store.is_valid("u1", "profile", "expired")
    assert await store.is_valid("u1", "profile", "unregistered")


async def test_activation_keeps_only_active_and_previous(catalog):
    first = await catalog.begin_version("doc", "hash-1", _metadata())
    await catalog.activate(first["version_id"])
    second = await catalog.begin_version("doc", "hash-2", _metadata())
    await catalog.activate(second["version_id"])
    third = await catalog.begin_version("doc", "hash-3", _metadata())

    class FakeKnowledgeBase:
        def __init__(self):
            self.deleted = []

        def delete_document(self, version_id):
            self.deleted.append(version_id)
            return 1

    knowledge_base = FakeKnowledgeBase()
    await knowledge_service._prune_versions_before_activation(
        knowledge_base, catalog, "doc", third["version_id"]
    )
    await catalog.activate(third["version_id"])
    versions = await catalog.list_versions("doc")
    assert [item["version_id"] for item in versions] == [
        third["version_id"],
        second["version_id"],
    ]
    assert knowledge_base.deleted == []
    async with transaction() as conn:
        jobs = (
            await conn.execute("SELECT * FROM jobs WHERE kind='knowledge_delete'")
        ).fetchall()
    assert len(jobs) == 1


async def test_ingest_queues_index_without_replacing_active(catalog):
    first = await catalog.begin_version("doc", "hash-1", _metadata())
    await catalog.activate(first["version_id"])
    count, pending = await knowledge_service._ingest_version(
        "doc", "新原文", {**_metadata(), "content_hash": "hash-2"}
    )
    assert count == 0
    assert (await catalog.active_version("doc"))["version_id"] == first["version_id"]
    assert (await catalog.get_version(pending["version_id"]))["content"] == "新原文"
    async with transaction() as conn:
        assert (
            await conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE kind='knowledge_index'"
            )
        ).fetchone()["count"] == 1


async def test_document_delete_records_outbox_atomically(catalog, monkeypatch):
    first = await catalog.begin_version("doc", "hash-1", _metadata())
    await catalog.activate(first["version_id"])
    second = await catalog.begin_version("doc", "hash-2", _metadata())
    await catalog.activate(second["version_id"])
    monkeypatch.setattr(knowledge_service, "MedicalKnowledgeBase", lambda: object())
    result = await knowledge_service.delete_document("doc")
    assert result.message == "delete_queued"
    assert await catalog.document_versions("doc") == []
    async with transaction() as conn:
        assert (
            await conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE kind='knowledge_delete'"
            )
        ).fetchone()["count"] == 2


@pytest.mark.asyncio
async def test_lifecycle_prunes_memory_only():

    class FakeCatalog:
        def __init__(self):
            self.finished = None

        async def create_job(self, *_args):
            return "job-1"

        async def finish_job(self, *args):
            self.finished = args

        async def audit(self, *_args):
            return None

        async def get_job(self, _job_id):
            return {"job_id": "job-1", "status": "completed"}

    fake_catalog = FakeCatalog()
    service = DataLifecycleService(catalog=fake_catalog)
    service._prune_memory_rows = AsyncMock(return_value={"memory_usage": 2})
    job = await service.prune_expired("admin")
    assert job["status"] == "completed"
    assert fake_catalog.finished[1] == "completed"
    assert fake_catalog.finished[2] == {"memory_usage": 2}


@pytest.mark.asyncio
async def test_lifecycle_deletes_user_data_and_records_job(catalog):
    from mediZJ.memory.session_db import SessionDB

    db = SessionDB()
    user = await db.get_or_create_user("patient")
    await db.upsert_profile(user["user_id"], content="档案")
    await db.save_turn("s", 0, {"content": "问"}, {"content": "答"}, user["user_id"])
    job = await DataLifecycleService(catalog=catalog, session_db=db).delete_user(
        user["user_id"], "admin"
    )
    assert job["status"] == "completed"
    assert await db.get_session("s", user["user_id"]) is None
    assert await db.get_profile(user["user_id"]) is None
    async with transaction() as conn:
        assert (
            await conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE kind IN ('cache_delete','session_delete')"
            )
        ).fetchone()["count"] == 2


@pytest.mark.asyncio
async def test_lifecycle_retry_routes_failed_job(monkeypatch):

    class FakeCatalog:
        async def get_job(self, _job_id):
            return {"status": "failed", "job_type": "delete_user", "target_id": "u1"}

    service = DataLifecycleService(catalog=FakeCatalog())
    service.delete_user = AsyncMock(return_value={"status": "completed"})
    assert (await service.retry("job", "admin"))["status"] == "completed"
    service.catalog.get_job = AsyncMock(return_value=None)
    with pytest.raises(LookupError, match="作业不存在"):
        await service.retry("missing", "admin")


async def test_expire_documents_marks_expired(catalog):
    version = await catalog.begin_version("doc", "hash-1", _expired_metadata())
    await catalog.activate(version["version_id"])
    assert await catalog.expire_documents(datetime.now(timezone.utc).isoformat()) == 1
    assert await catalog.active_version("doc") is None
    assert (await catalog.get_version(version["version_id"]))["status"] == "expired"
    visible = await catalog.list_active_and_expired()
    assert any(
        (
            item["version_id"] == version["version_id"] and item["status"] == "expired"
            for item in visible
        )
    )


async def test_version_chain_when_active_expired(catalog):
    v1 = await catalog.begin_version("doc", "hash-1", _metadata())
    await catalog.activate(v1["version_id"])
    v2 = await catalog.begin_version("doc", "hash-2", _expired_metadata())
    await catalog.activate(v2["version_id"])
    await catalog.expire_documents(datetime.now(timezone.utc).isoformat())
    assert (await catalog.get_version(v2["version_id"]))["status"] == "expired"
    v3 = await catalog.begin_version("doc", "hash-3", _metadata())
    assert v3["supersedes_version_id"] == v2["version_id"]
    await catalog.activate(v3["version_id"])
    assert (await catalog.get_version(v2["version_id"]))["status"] == "archived"
    assert (await catalog.get_version(v3["version_id"]))["status"] == "active"


async def test_citation_validator_rejects_expired(catalog):
    version = await catalog.begin_version("doc", "hash-1", _expired_metadata())
    await catalog.activate(version["version_id"])
    await catalog.expire_documents(datetime.now(timezone.utc).isoformat())
    validator = CitationValidator(catalog=catalog, knowledge_base=None)
    assert (
        await validator.validate(
            [{"index": 1, "doc_id": "doc", "version_id": version["version_id"]}]
        )
        == []
    )


def _expired_metadata():
    return {**_metadata(), "expires_at": "2020-01-01T00:00:00+00:00"}
