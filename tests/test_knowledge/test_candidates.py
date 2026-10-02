"""知识候选审核与可信证据门槛测试。"""

from types import SimpleNamespace
from unittest.mock import patch
import pytest

from mediZJ.knowledge.catalog import KnowledgeCatalog
from mediZJ.knowledge.candidate_service import KnowledgeCandidateService

pytestmark = [pytest.mark.integration, pytest.mark.infrastructure]


@pytest.fixture
def service(mysql_infrastructure):
    KnowledgeCatalog.reset()
    catalog = KnowledgeCatalog()
    yield KnowledgeCandidateService(catalog)
    KnowledgeCatalog.reset()


async def test_unverified_candidate_cannot_be_approved(service):
    candidate = await service.add_candidate("turn-1", "user-1", "某主张", "原文", [])
    assert candidate["status"] == "unverified"
    with pytest.raises(ValueError, match="不可批准"):
        await service.approve(candidate["candidate_id"], "admin")


async def test_duplicate_turn_claim_is_idempotent(service):
    first = await service.add_candidate("turn-1", "user-1", "某主张", "原文", [])
    second = await service.add_candidate("turn-1", "user-1", "某主张", "原文", [])
    assert first["candidate_id"] == second["candidate_id"]


async def test_duplicate_claim_across_turns_is_reused(service):
    first = await service.add_candidate("turn-1", "user-1", "某主张", "原文", [])
    second = await service.add_candidate("turn-2", "user-2", "某主张", "另一原文", [])
    assert first["candidate_id"] == second["candidate_id"]


async def test_trust_requires_active_version_and_source_url(service):
    version = await service.catalog.begin_version(
        "doc", "hash-1", {"filename": "doc.txt", "source": "upload"}
    )
    await service.catalog.activate(version["version_id"])
    with pytest.raises(ValueError, match="URL"):
        await service.trust_source("doc", "invalid", "admin")
    await service.trust_source("doc", "https://example.org/guide", "admin")
    assert await service.trusted_source(version["version_id"]) is not None
    replacement = await service.catalog.begin_version(
        "doc", "hash-2", {"filename": "doc.txt", "source": "upload"}
    )
    await service.catalog.activate(replacement["version_id"])
    assert await service.trusted_source(version["version_id"]) is None


async def test_evidence_search_uses_only_trusted_hits(service):
    hit = SimpleNamespace(metadata={"version_id": "v1"}, content="可靠来源片段")
    with (
        patch(
            "mediZJ.knowledge.candidate_service.search_knowledge", return_value=[hit]
        ),
        patch.object(service, "trusted_source", return_value=None),
    ):
        assert await service.evidence_hits("主张") == []
    with (
        patch(
            "mediZJ.knowledge.candidate_service.search_knowledge", return_value=[hit]
        ),
        patch.object(
            service,
            "trusted_source",
            return_value={"document_id": "doc", "source_url": "https://example.org"},
        ),
    ):
        assert (await service.evidence_hits("主张"))[0]["version_id"] == "v1"


async def test_conflict_candidate_cannot_be_approved(service):
    candidate = await service.add_candidate(
        "turn-1",
        "user-1",
        "某主张",
        "原文",
        [{"verdict": "conflict", "version_id": "v1"}],
    )
    assert candidate["status"] == "conflict"
    with pytest.raises(ValueError, match="不可批准"):
        await service.approve(candidate["candidate_id"], "admin")


async def test_approved_only_after_successful_ingest(service):
    evidence = [
        {
            "verdict": "support",
            "version_id": "v1",
            "source_url": "https://example.org/source",
        }
    ]
    candidate = await service.add_candidate(
        "turn-1", "user-1", "某主张", "原文", evidence
    )
    with (
        patch.object(
            service,
            "trusted_source",
            return_value={
                "version_id": "v1",
                "source_url": "https://example.org/source",
            },
        ),
        patch("mediZJ.knowledge.candidate_service.upload_document") as upload,
    ):
        upload.return_value = SimpleNamespace(doc_id="general_reviewed")
        approved = await service.approve(candidate["candidate_id"], "admin")
    assert approved["status"] == "approved"
    assert approved["document_id"] == "general_reviewed"
    with pytest.raises(ValueError, match="不可批准"):
        await service.approve(candidate["candidate_id"], "admin")


async def test_failed_ingest_leaves_candidate_pending(service):
    candidate = await service.add_candidate(
        "turn-1",
        "user-1",
        "某主张",
        "原文",
        [
            {
                "verdict": "support",
                "version_id": "v1",
                "source_url": "https://example.org/source",
            }
        ],
    )
    with (
        patch.object(
            service,
            "trusted_source",
            return_value={
                "version_id": "v1",
                "source_url": "https://example.org/source",
            },
        ),
        patch(
            "mediZJ.knowledge.candidate_service.upload_document",
            side_effect=RuntimeError("ingest failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="ingest failed"):
            await service.approve(candidate["candidate_id"], "admin")
    assert (await service.get_candidate(candidate["candidate_id"]))[
        "status"
    ] == "pending_review"


async def test_approval_rechecks_current_evidence(service):
    candidate = await service.add_candidate(
        "turn-1",
        "user-1",
        "某主张",
        "原文",
        [
            {
                "verdict": "support",
                "version_id": "v1",
                "source_url": "https://example.org/source",
            }
        ],
    )
    with pytest.raises(ValueError, match="缺少"):
        await service.approve(candidate["candidate_id"], "admin")
    with patch.object(
        service,
        "trusted_source",
        return_value={"version_id": "v1", "source_url": "https://changed.example.org"},
    ):
        with pytest.raises(ValueError, match="已变化"):
            await service.approve(candidate["candidate_id"], "admin")


async def test_rejection_is_final(service):
    candidate = await service.add_candidate("turn-1", "user-1", "某主张", "原文", [])
    rejected = await service.reject(candidate["candidate_id"], "admin")
    assert rejected["status"] == "rejected"
    with pytest.raises(ValueError, match="已完成"):
        await service.reject(candidate["candidate_id"], "admin")


async def test_unverified_candidate_can_be_rechecked(service):
    candidate = await service.add_candidate("turn-1", "user-1", "某主张", "原文", [])
    updated = await service.update_evidence(
        candidate["candidate_id"],
        [
            {
                "verdict": "support",
                "version_id": "v1",
                "source_url": "https://example.org/source",
            }
        ],
    )
    assert updated["status"] == "pending_review"
    updated = await service.update_evidence(
        candidate["candidate_id"],
        [
            {
                "verdict": "conflict",
                "version_id": "v1",
                "source_url": "https://example.org/source",
            }
        ],
    )
    assert updated["status"] == "conflict"


def test_candidate_review_requires_admin():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mediZJ.api.auth import get_current_user
    from mediZJ.api.routers.knowledge import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-1",
        "role": "user",
    }
    with TestClient(app) as client:
        assert client.get("/api/knowledge/candidates").status_code == 403
        assert (
            client.post("/api/knowledge/candidates/example/approve").status_code == 403
        )
