"""自进化闭环测试。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest

from mediZJ.evolution.judge import ConversationJudge
from mediZJ.evolution.service import EvolutionService
from mediZJ.evolution.source_catalog import read_source_snippet
from mediZJ.evolution.storage import EvolutionStorage
from mediZJ.evolution.storage import RollbackBlockedError
from mediZJ.memory.session_db import SessionDB


@pytest.fixture
def evolution(tmp_path):
    db_path = str(tmp_path / "evolution.db")
    SessionDB.reset()
    EvolutionStorage.reset()
    EvolutionService.reset()
    session_db = SessionDB(db_path)
    storage = EvolutionStorage(db_path)
    service = EvolutionService(storage=storage)
    yield session_db, storage, service
    session_db._get_conn().close()
    storage._get_conn().close()
    EvolutionService.reset()
    EvolutionStorage.reset()
    SessionDB.reset()


def _save_answer(session_db: SessionDB, user_id: str = "patient") -> int:
    saved = session_db.save_turn(
        session_id="session-1",
        turn_index=0,
        user_msg={"content": "头痛怎么办？"},
        assistant_msg={
            "content": "建议先评估危险信号。",
            "trace_id": "trace-1",
        },
        user_id=user_id,
    )
    return int(saved["assistant_message_id"])


def test_feedback_is_isolated_and_enqueues_job(evolution):
    session_db, storage, service = evolution
    message_id = _save_answer(session_db)

    feedback = service.submit_feedback(
        message_id,
        "patient",
        "dislike",
        ["incomplete"],
        "缺少就医建议",
    )

    assert feedback["version"] == 1
    assert feedback["evaluation_job_id"]
    assert storage.get_feedback(message_id, "patient")["rating"] == "dislike"
    updated = service.submit_feedback(
        message_id,
        "patient",
        "like",
        [],
        "",
    )
    assert updated["version"] == 2
    assert storage.get_feedback(message_id, "another-user") is None
    with pytest.raises(LookupError):
        service.submit_feedback(message_id, "another-user", "like", [], "")


def test_high_scores_promote_private_experience_after_two_supports(evolution):
    session_db, storage, _service = evolution
    first_message = _save_answer(session_db)
    second = session_db.save_turn(
        session_id="session-2",
        turn_index=0,
        user_msg={"content": "偏头痛怎么办？"},
        assistant_msg={"content": "先排查红旗征象。"},
        user_id="patient",
    )
    result = {
        "overall_score": 90,
        "dimension_scores": {"medical_safety": 5},
        "verdict": "high",
        "attribution": [],
        "experience": {
            "type": "response_strategy",
            "scope": "private",
            "query_pattern": "头痛",
            "content": "先筛查红旗征象，再给出分层建议。",
        },
    }
    for index, message_id in enumerate(
        [first_message, int(second["assistant_message_id"])]
    ):
        job_id = storage.enqueue_job(
            message_id,
            "patient",
            "manual",
            index + 1,
        )
        job = storage.claim_job()
        assert job and job["job_id"] == job_id
        storage.save_evaluation(job, result, "fake-judge")
        storage.complete_job(job_id)

    experiences = storage.list_experiences()
    assert experiences[0]["status"] == "active"
    assert experiences[0]["support_count"] == 2
    assert storage.list_releases()[0]["action"] == "auto_promote"


def test_session_deletion_recalculates_and_demotes_experience(evolution):
    session_db, storage, _service = evolution
    first_message = _save_answer(session_db)
    conn = storage._get_conn()
    conn.execute(
        """
        CREATE TABLE traces (
            trace_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            tree_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO traces VALUES ('trace-1', 'session-1', '{}')"
    )
    conn.commit()
    second = session_db.save_turn(
        session_id="session-2",
        turn_index=0,
        user_msg={"content": "偏头痛怎么办？"},
        assistant_msg={"content": "先排查红旗征象。"},
        user_id="patient",
    )
    result = {
        "overall_score": 90,
        "dimension_scores": {"medical_safety": 5},
        "verdict": "high",
        "attribution": [],
        "experience": {
            "type": "response_strategy",
            "scope": "private",
            "query_pattern": "头痛",
            "content": "先筛查红旗征象，再给出分层建议。",
        },
    }
    for index, message_id in enumerate(
        [first_message, int(second["assistant_message_id"])]
    ):
        job_id = storage.enqueue_job(
            message_id,
            "patient",
            "manual",
            index + 1,
        )
        job = storage.claim_job()
        storage.save_evaluation(job, result, "fake-judge")
        storage.complete_job(job_id)

    experience_id = storage.list_experiences()[0]["experience_id"]
    storage.record_exposures(
        first_message,
        "patient",
        [
            {
                "experience_id": experience_id,
                "bucket": "active",
                "applied": True,
            }
        ],
    )

    deletion = storage.delete_session_data("session-1", "patient")

    assert deletion is not None
    experience = storage.list_experiences()[0]
    assert experience["status"] == "candidate"
    assert experience["support_count"] == 1
    assert experience["distinct_users"] == 1
    assert experience["average_score"] == 90
    assert deletion["demoted_experience_ids"] == [
        experience["experience_id"]
    ]
    assert session_db.get_session("session-1", "patient") is None
    assert session_db.get_session("session-2", "patient") is not None
    assert storage.list_releases()[0]["action"] == (
        "auto_demote_session_deleted"
    )

    audit = storage._get_conn().execute(
        "SELECT * FROM session_deletion_audits"
    ).fetchone()
    assert audit["deleted_message_count"] == 2
    assert audit["deleted_evaluation_count"] == 1
    assert audit["deleted_trace_count"] == 1
    assert audit["affected_experience_ids"] == json.dumps(
        [experience["experience_id"]]
    )
    assert "session-1" not in audit["session_id_hash"]
    assert storage._get_conn().execute(
        "SELECT COUNT(*) FROM traces"
    ).fetchone()[0] == 0
    assert storage._get_conn().execute(
        "SELECT COUNT(*) FROM experience_exposures"
    ).fetchone()[0] == 0


def test_session_deletion_removes_failure_and_is_idempotent(evolution):
    session_db, storage, _service = evolution
    message_id = _save_answer(session_db)
    job_id = storage.enqueue_job(message_id, "patient", "manual", 1)
    job = storage.claim_job()
    storage.save_evaluation(
        job,
        {
            "overall_score": 40,
            "dimension_scores": {"medical_safety": 2},
            "verdict": "low",
            "safety_violation": True,
            "attribution": ["prompt"],
        },
        "fake-judge",
    )
    storage.complete_job(job_id)

    assert storage.delete_session_data("session-1", "patient") is not None

    assert storage.list_evaluations() == []
    assert storage.list_failures() == []
    assert storage.delete_session_data("session-1", "patient") is None


def test_session_deletion_retires_experience_without_sources(evolution):
    session_db, storage, _service = evolution
    message_id = _save_answer(session_db)
    job_id = storage.enqueue_job(message_id, "patient", "manual", 1)
    job = storage.claim_job()
    storage.save_evaluation(
        job,
        {
            "overall_score": 92,
            "dimension_scores": {"medical_safety": 5},
            "verdict": "high",
            "attribution": [],
            "experience": {
                "type": "response_strategy",
                "scope": "private",
                "query_pattern": "头痛",
                "content": "先筛查红旗征象。",
            },
        },
        "fake-judge",
    )
    storage.complete_job(job_id)

    assert storage.delete_session_data("session-1", "patient") is not None

    experience = storage.list_experiences()[0]
    assert experience["status"] == "retired"
    assert experience["support_count"] == 0
    assert experience["distinct_users"] == 0
    assert experience["average_score"] == 0


def test_session_service_completes_external_cleanup_audit(
    evolution,
    monkeypatch,
    tmp_path,
):
    from mediZJ.api.services import session_service

    session_db, storage, _service = evolution
    _save_answer(session_db)

    class FakeVectors:
        deleted_session_id = None

        def delete_session(self, session_id):
            self.deleted_session_id = session_id

    vectors = FakeVectors()
    monkeypatch.setattr(session_service, "_db", session_db)
    monkeypatch.setattr(session_service, "_vectors", vectors)
    monkeypatch.setattr(session_service, "SUMMARY_DIR", str(tmp_path))

    assert session_service.delete_session("session-1", "patient") is True

    assert vectors.deleted_session_id == "session-1"
    audit = storage._get_conn().execute(
        "SELECT cleanup_status, cleanup_errors "
        "FROM session_deletion_audits"
    ).fetchone()
    assert audit["cleanup_status"] == "completed"
    assert json.loads(audit["cleanup_errors"]) == []


@pytest.mark.asyncio
async def test_judge_applies_dislike_and_safety_gates():
    class FakeLLM:
        model_name = "fake"

        async def chat(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "dimension_scores": {
                        "medical_safety": 5,
                        "accuracy_evidence": 5,
                        "completeness": 5,
                        "tool_use": 5,
                        "routing": 5,
                        "personalization": 5,
                        "clarity": 5,
                    },
                    "attribution": ["prompt", "invalid"],
                    "safety_violation": False,
                }
            )

    result = await ConversationJudge(FakeLLM()).evaluate(
        {"question": "q", "content": "a", "feedback": {"rating": "dislike"}}
    )

    assert result["overall_score"] == 100
    assert result["verdict"] == "low"
    assert result["attribution"] == ["prompt"]


@pytest.mark.asyncio
async def test_judge_tolerates_malformed_structured_fields():
    class FakeLLM:
        model_name = "fake"

        async def chat(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "dimension_scores": {"medical_safety": {"score": 5}},
                    "attribution": [{"type": "retrieval"}],
                    "experiences": [
                        {
                            "type": {"value": "response_strategy"},
                            "query_pattern": "头痛",
                            "content": "先筛查危险信号",
                        },
                        {
                            "type": "response_strategy",
                            "query_pattern": "头痛",
                            "content": "先筛查危险信号",
                            "risk_level": {"value": "low"},
                        },
                    ],
                }
            )

    result = await ConversationJudge(FakeLLM()).evaluate(
        {"question": "q", "content": "a", "user_id": "patient"}
    )

    assert result["dimension_scores"]["medical_safety"] == 0
    assert result["attribution"] == ["other"]
    assert len(result["experiences"]) == 1
    assert result["experiences"][0]["risk_level"] == "medium"


def test_runtime_context_only_uses_matching_active_experiences(evolution):
    _session_db, storage, service = evolution
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, owner_user_id,
             query_pattern, content, status, created_at, updated_at)
        VALUES ('exp-1', 'response_strategy', 'private', 'patient',
                '头痛', '检查红旗征象', 'active', 'now', 'now')
        """
    )
    conn.commit()

    context = service.get_runtime_context("patient", "我最近头痛")

    assert context["applied_experience_ids"] == ["exp-1"]
    assert "检查红旗征象" in context["verified_experiences"]
    assert service.get_runtime_context("other", "我最近头痛") == {}


@pytest.mark.asyncio
async def test_worker_saves_low_evaluation_and_failure(evolution):
    session_db, storage, service = evolution
    message_id = _save_answer(session_db)
    storage.enqueue_job(message_id, "patient", "manual", 10)

    class FakeJudge:
        model_name = "fake"

        async def evaluate(self, _context):
            return {
                "overall_score": 40,
                "dimension_scores": {"medical_safety": 2},
                "verdict": "low",
                "safety_violation": True,
                "attribution": ["retrieval"],
                "evidence": ["未排查危险征象"],
                "recommendations": ["增加红旗征象检查"],
            }

    service._judge = FakeJudge()
    assert await service.process_one() is True
    assert await service.process_one() is False
    evaluation = storage.list_evaluations()[0]
    assert evaluation["verdict"] == "low"
    assert evaluation["question"] == "头痛怎么办？"
    assert evaluation["answer"] == "建议先评估危险信号。"
    assert evaluation["session_id"] == "session-1"
    assert evaluation["trace_id"] == "trace-1"
    assert evaluation["trigger_type"] == "manual"
    failure = storage.list_failures()[0]
    assert failure["status"] == "open"
    assert failure["question"] == "头痛怎么办？"
    assert failure["trace_id"] == "trace-1"
    assert failure["root_causes"] == ["retrieval"]
    assert {item["source_id"] for item in failure["source_locations"]} == {
        "retrieval.memory",
        "retrieval.knowledge",
    }
    assert storage.overview()["failure_count"] == 1


@pytest.mark.asyncio
async def test_worker_retries_failed_judge(evolution):
    session_db, storage, service = evolution
    message_id = _save_answer(session_db)
    storage.enqueue_job(message_id, "patient", "manual", 11)

    class BrokenJudge:
        model_name = "broken"

        async def evaluate(self, _context):
            raise RuntimeError("评审器不可用")

    service._judge = BrokenJudge()
    assert await service.process_one() is True
    job = storage.claim_job()
    assert job["attempts"] == 1
    storage.fail_job(job["job_id"], "still broken", max_attempts=2)
    assert storage.claim_job() is None


@pytest.mark.asyncio
async def test_worker_times_out_slow_judge(evolution, monkeypatch):
    session_db, storage, _service = evolution
    message_id = _save_answer(session_db)
    storage.enqueue_job(message_id, "patient", "manual", 12)
    monkeypatch.setenv("EVOLUTION_JUDGE_TIMEOUT", "0.01")
    EvolutionService.reset()
    service = EvolutionService(storage=storage)

    class SlowJudge:
        model_name = "slow"

        async def evaluate(self, _context):
            await asyncio.sleep(1)
            return {}

    service._judge = SlowJudge()

    assert await service.process_one() is True
    job = storage.list_jobs()[0]
    assert job["status"] == "pending"
    assert job["attempts"] == 1


def test_manual_queue_sampling_and_release_rollback(evolution, monkeypatch):
    session_db, storage, service = evolution
    message_id = _save_answer(session_db)
    manual_id = service.enqueue_manual(message_id)
    assert manual_id
    with pytest.raises(LookupError):
        service.enqueue_manual(999999)

    monkeypatch.setenv("EVOLUTION_SAMPLE_RATE", "1")
    service.maybe_enqueue_sample(message_id, "patient")

    conn = storage._get_conn()
    for experience_id in ("exp-active", "exp-candidate"):
        conn.execute(
            """
            INSERT INTO learned_experiences
                (experience_id, experience_type, scope, owner_user_id,
                 query_pattern, content, status, average_score,
                 support_count, distinct_users, created_at, updated_at)
            VALUES (?, 'response_strategy', 'global', NULL,
                    '头痛', '安全建议', 'candidate', 90,
                    3, 3, 'now', 'now')
            """,
            (experience_id,),
        )
    conn.commit()
    assert storage.set_experience_status("exp-active", "active", "admin")
    first_version = storage.list_releases()[0]["version"]
    assert storage.set_experience_status("exp-candidate", "active", "admin")
    assert storage.rollback_release(first_version, "admin")
    active_ids = {
        item["experience_id"]
        for item in storage.get_active_experiences("patient")
    }
    assert active_ids == {"exp-active"}
    assert storage.set_experience_status("missing", "active", "admin") is False
    assert storage.rollback_release(999999, "admin") is False


def test_global_single_case_cannot_be_published(evolution):
    _session_db, storage, _service = evolution
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             created_at, updated_at)
        VALUES ('single-case', 'response_strategy', 'global', '高血压',
                '按结构回答', 'candidate', 98, 1, 1, 'now', 'now')
        """
    )
    conn.commit()

    with pytest.raises(ValueError, match="3 个不同用户"):
        storage.set_experience_status("single-case", "active", "admin")
    item = storage.list_experiences()[0]
    assert item["publishable"] is False
    assert item["status"] == "candidate"


def test_global_support_threshold_is_configurable(evolution, monkeypatch):
    """全局经验的最少支持用户数可通过 EVOLUTION_GLOBAL_MIN_SUPPORT 调整。"""
    _session_db, storage, _service = evolution
    monkeypatch.setenv("EVOLUTION_GLOBAL_MIN_SUPPORT", "2")
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             created_at, updated_at)
        VALUES ('enough', 'response_strategy', 'global', '头痛',
                '按结构回答', 'candidate', 90, 2, 2, 'now', 'now')
        """
    )
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             created_at, updated_at)
        VALUES ('one-user', 'response_strategy', 'global', '发热',
                '按结构回答', 'candidate', 90, 3, 1, 'now', 'now')
        """
    )
    conn.commit()

    # 阈值降为 2 后，2 个不同用户即可进入观察
    assert storage.set_experience_status("enough", "active", "admin") is True
    # 3 条支持但仅 1 个不同用户仍被拒绝，distinct_users 是硬约束
    with pytest.raises(ValueError, match="全局经验至少需 2 个不同用户"):
        storage.set_experience_status("one-user", "active", "admin")


def test_observing_experience_retires_after_negative_feedback(evolution):
    session_db, storage, service = evolution
    message_id = _save_answer(session_db)
    conn = storage._get_conn()
    conn.execute(
        "CREATE TABLE traces (trace_id TEXT PRIMARY KEY, tree_json TEXT)"
    )
    conn.execute(
        "INSERT INTO traces VALUES (?, ?)",
        (
            "trace-1",
            json.dumps(
                {"trace_attrs": {"applied_experience_ids": ["observing-exp"]}}
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             created_at, updated_at)
        VALUES ('observing-exp', 'response_strategy', 'global', '头痛',
                '安全回答', 'observing', 90, 3, 3, 'now', 'now')
        """
    )
    conn.commit()
    storage.record_exposures(
        message_id,
        "patient",
        [
            {
                "experience_id": "observing-exp",
                "bucket": "treatment",
                "applied": True,
            }
        ],
    )

    service.submit_feedback(message_id, "patient", "dislike", ["unsafe"], "")

    item = storage.list_experiences()[0]
    assert item["status"] == "retired"
    assert item["negative_count"] == 1
    assert storage.list_releases()[0]["action"] == (
        "auto_retire_negative_feedback"
    )


@pytest.mark.asyncio
async def test_judge_caps_answer_and_discards_medical_knowledge():
    class FakeLLM:
        model_name = "fake"

        async def chat(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "dimension_scores": {
                        "medical_safety": 5,
                        "accuracy_evidence": 5,
                        "completeness": 5,
                        "tool_use": 5,
                        "routing": 5,
                        "personalization": 5,
                        "clarity": 5,
                    },
                    "numeric_medical_claims": True,
                    "authoritative_sources_present": False,
                    "personalization_required": True,
                    "personalization_addressed": False,
                    "attribution": [],
                    "experiences": [
                        {
                            "type": "retrieval_hint",
                            "scope": "global",
                            "query_pattern": "高血压数值建议",
                            "content": "使用 search-knowledge 查询",
                            "risk_level": "medium",
                        },
                        {
                            "type": "medical_knowledge",
                            "scope": "global",
                            "query_pattern": "高血压饮食",
                            "content": "每日限盐",
                            "risk_level": "high",
                        },
                    ],
                }
            )

    result = await ConversationJudge(FakeLLM()).evaluate(
        {"question": "q", "content": "a", "user_id": "patient"}
    )

    assert result["overall_score"] == 79
    assert result["verdict"] == "medium"
    assert len(result["experiences"]) == 1
    assert "search-knowledge" not in result["experiences"][0]["content"]


@pytest.mark.asyncio
async def test_judge_creates_strategy_for_retrieval_issue():
    class FakeLLM:
        model_name = "fake"

        async def chat(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "dimension_scores": {
                        "medical_safety": 5,
                        "accuracy_evidence": 4,
                        "completeness": 5,
                        "tool_use": 4,
                        "routing": 5,
                        "personalization": 5,
                        "clarity": 5,
                    },
                    "attribution": ["retrieval"],
                    "recommendations": ["过滤无关结果并校验引用"],
                    "experiences": [],
                }
            )

    result = await ConversationJudge(FakeLLM()).evaluate(
        {"question": "头痛发热怎么办", "content": "回答", "user_id": "patient"}
    )

    assert result["recommendations"] == ["过滤无关结果并校验引用"]
    assert result["experiences"][0]["type"] == "retrieval_hint"
    assert "过滤无关结果" in result["experiences"][0]["content"]


def test_storage_does_not_record_medical_knowledge(evolution):
    session_db, storage, _service = evolution
    message_id = _save_answer(session_db)
    job_id = storage.enqueue_job(message_id, "patient", "manual", 1)
    job = storage.claim_job()
    assert job and job["job_id"] == job_id

    storage.save_evaluation(
        job,
        {
            "overall_score": 90,
            "dimension_scores": {"medical_safety": 5},
            "verdict": "high",
            "attribution": [],
            "experiences": [
                {
                    "type": "medical_knowledge",
                    "scope": "global",
                    "query_pattern": "高血压饮食",
                    "content": "每日限盐",
                }
            ],
        },
        "fake-judge",
    )

    assert storage.list_experiences() == []
    extracted = storage.list_evaluations()[0]["extracted_experience"]
    assert json.loads(extracted) == []


def test_only_rejected_experience_can_be_deleted(evolution):
    _session_db, storage, _service = evolution
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, created_at, updated_at)
        VALUES ('delete-exp', 'response_strategy', 'private', '头痛',
                '先检查危险信号', 'candidate', 'now', 'now')
        """
    )
    conn.commit()

    with pytest.raises(ValueError, match="仅已驳回的经验可以删除"):
        storage.apply_experience_action("delete-exp", "delete", "admin")

    assert storage.apply_experience_action(
        "delete-exp",
        "reject",
        "admin",
    )
    assert storage.apply_experience_action(
        "delete-exp",
        "reapply",
        "admin",
    )
    assert storage.list_experiences()[0]["status"] == "candidate"
    with pytest.raises(ValueError, match="仅已驳回的经验可以重新应用"):
        storage.apply_experience_action("delete-exp", "reapply", "admin")
    assert storage.apply_experience_action(
        "delete-exp",
        "reject",
        "admin",
    )
    assert storage.apply_experience_action(
        "delete-exp",
        "delete",
        "admin",
    )

    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, created_at, updated_at)
        VALUES ('retired-exp', 'response_strategy', 'private', '头痛',
                '已停用策略', 'retired', 'now', 'now')
        """
    )
    conn.commit()
    assert storage.apply_experience_action(
        "retired-exp",
        "reject",
        "admin",
    )
    assert storage.list_experiences()[0]["status"] == "rejected"
    assert storage.apply_experience_action(
        "retired-exp",
        "delete",
        "admin",
    )
    assert storage.list_experiences() == []


def test_judge_deidentifies_global_experience():
    text = "姓名：张三，手机号13812345678，证件11010519900101123X user-1"
    cleaned = ConversationJudge.deidentify(text, "user-1")
    assert "张三" not in cleaned
    assert "13812345678" not in cleaned
    assert "11010519900101123X" not in cleaned
    assert "user-1" not in cleaned


def test_source_snippet_is_limited_to_catalog():
    snippet = read_source_snippet("routing.supervisor", radius=5)
    assert snippet["path"] == "mediZJ/lgraph/supervisor_graph.py"
    assert "_route_by_subtask_count" in snippet["content"]
    assert snippet["end_line"] - snippet["start_line"] <= 10
    with pytest.raises(LookupError):
        read_source_snippet("../../.env")


def test_same_answer_only_counts_as_one_experience_support(evolution):
    session_db, storage, _service = evolution
    message_id = _save_answer(session_db)
    result = {
        "overall_score": 92,
        "dimension_scores": {"medical_safety": 5},
        "verdict": "high",
        "experiences": [
            {
                "type": "response_strategy",
                "scope": "private",
                "query_pattern": "头痛",
                "content": "先排查红旗征象",
                "risk_level": "low",
            }
        ],
    }
    for version in (101, 102):
        storage.enqueue_job(message_id, "patient", "manual", version)
        job = storage.claim_job()
        storage.save_evaluation(job, result, "fake")

    experience = storage.list_experiences()[0]
    assert experience["support_count"] == 1
    assert experience["status"] == "candidate"


def test_concurrent_workers_only_claim_job_once(evolution):
    session_db, storage, _service = evolution
    message_id = _save_answer(session_db)
    storage.enqueue_job(message_id, "patient", "manual", 201)
    barrier = Barrier(2)

    def claim():
        barrier.wait()
        job = storage.claim_job()
        storage._get_conn().close()
        storage._local.conn = None
        return job

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(lambda _index: claim(), range(2)))

    assert sum(job is not None for job in jobs) == 1


def test_old_feedback_job_is_superseded_without_side_effects(evolution):
    session_db, storage, service = evolution
    message_id = _save_answer(session_db)
    first = service.submit_feedback(
        message_id,
        "patient",
        "dislike",
        ["unsafe"],
        "旧反馈",
    )
    second = service.submit_feedback(message_id, "patient", "like", [], "")

    jobs = {job["feedback_version"]: job for job in storage.list_jobs()}
    assert jobs[first["version"]]["status"] == "superseded"
    assert jobs[second["version"]]["status"] == "pending"
    claimed = storage.claim_job()
    assert claimed["feedback_version"] == second["version"]
    assert json.loads(claimed["feedback_snapshot"])["rating"] == "like"


def test_control_feedback_does_not_retire_experience(evolution):
    session_db, storage, service = evolution
    message_id = _save_answer(session_db)
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             created_at, updated_at)
        VALUES ('control-exp', 'response_strategy', 'global', '头痛',
                '安全回答', 'observing', 90, 3, 3, 'now', 'now')
        """
    )
    conn.commit()
    storage.record_exposures(
        message_id,
        "patient",
        [{"experience_id": "control-exp", "bucket": "control", "applied": False}],
    )

    service.submit_feedback(message_id, "patient", "dislike", ["unsafe"], "")

    experience = storage.list_experiences()[0]
    assert experience["status"] == "observing"
    assert experience["negative_count"] == 0


@pytest.mark.asyncio
async def test_global_experience_with_personal_data_is_forced_private():
    class FakeLLM:
        model_name = "fake"

        async def chat(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "dimension_scores": {
                        "medical_safety": 5,
                        "accuracy_evidence": 5,
                        "completeness": 5,
                        "tool_use": 5,
                        "routing": 5,
                        "personalization": 5,
                        "clarity": 5,
                    },
                    "experiences": [
                        {
                            "type": "response_strategy",
                            "scope": "global",
                            "query_pattern": "患者手机号 13812345678",
                            "content": "先进行安全分层",
                            "risk_level": "low",
                        }
                    ],
                }
            )

    result = await ConversationJudge(FakeLLM()).evaluate(
        {"question": "q", "content": "a", "user_id": "patient"}
    )

    assert result["experiences"][0]["scope"] == "private"


def test_publication_rechecks_personal_data_in_all_fields(evolution):
    _session_db, storage, _service = evolution
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             prerequisites, created_at, updated_at)
        VALUES ('pii-exp', 'response_strategy', 'global', '头痛',
                '先做安全分层', 'candidate', 90, 3, 3,
                '["联系 13812345678"]', 'now', 'now')
        """
    )
    conn.commit()

    with pytest.raises(ValueError, match="个人身份信息"):
        storage.apply_experience_action("pii-exp", "observe", "admin")


def test_medical_knowledge_requires_trusted_evidence_and_expiry(evolution):
    _session_db, storage, _service = evolution
    conn = storage._get_conn()
    expires_at = (datetime.now() + timedelta(days=30)).isoformat()
    evidence = json.dumps(
        [
            {
                "doc_id": "guide-1",
                "source": "临床指南数据库",
                "content": "指南原文",
            }
        ],
        ensure_ascii=False,
    )
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             prerequisites, safety_notes, evidence_refs, risk_level,
             expires_at, created_at, updated_at)
        VALUES ('medical-exp', 'medical_knowledge', 'global', '高血压',
                '依据指南评估', 'candidate', 90, 3, 3, '["确认诊断"]',
                '不能替代医生', ?, 'high', ?, 'now', 'now')
        """,
        (evidence, expires_at),
    )
    conn.commit()

    assert storage.apply_experience_action("medical-exp", "observe", "admin")


def test_rollback_rejects_currently_unsafe_experience(evolution):
    _session_db, storage, _service = evolution
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             created_at, updated_at)
        VALUES ('unsafe-exp', 'response_strategy', 'global', '头痛',
                '先做安全分层', 'active', 90, 5, 5, 'now', 'now')
        """
    )
    storage._create_release(conn, "activate", "admin")
    version = storage.list_releases()[0]["version"]
    conn.execute(
        "UPDATE learned_experiences SET status = 'retired', negative_count = 1 "
        "WHERE experience_id = 'unsafe-exp'"
    )
    conn.commit()

    with pytest.raises(RollbackBlockedError) as exc_info:
        storage.rollback_release(version, "admin")

    assert exc_info.value.blockers[0]["experience_id"] == "unsafe-exp"
    assert storage.list_experiences()[0]["status"] == "retired"


def test_global_experience_requires_observation_before_activation(evolution):
    session_db, storage, _service = evolution
    conn = storage._get_conn()
    conn.execute(
        """
        INSERT INTO learned_experiences
            (experience_id, experience_type, scope, query_pattern, content,
             status, average_score, support_count, distinct_users,
             created_at, updated_at)
        VALUES ('observe-exp', 'response_strategy', 'global', '头痛',
                '先做安全分层', 'candidate', 90, 3, 3, 'now', 'now')
        """
    )
    conn.commit()

    with pytest.raises(ValueError, match="必须先完成观察"):
        storage.apply_experience_action("observe-exp", "activate", "admin")
    assert storage.apply_experience_action(
        "observe-exp",
        "observe",
        "admin",
    )

    for index in range(10):
        user_id = f"observer-{index}"
        saved = session_db.save_turn(
            session_id=f"observe-session-{index}",
            turn_index=0,
            user_msg={"content": "头痛怎么办"},
            assistant_msg={"content": "先做安全分层"},
            user_id=user_id,
        )
        message_id = int(saved["assistant_message_id"])
        bucket = "treatment" if index < 5 else "control"
        storage.record_exposures(
            message_id,
            user_id,
            [
                {
                    "experience_id": "observe-exp",
                    "bucket": bucket,
                    "applied": bucket == "treatment",
                }
            ],
        )
        storage.enqueue_job(message_id, user_id, "manual", index + 1000)
        job = storage.claim_job()
        storage.save_evaluation(
            job,
            {
                "overall_score": 90,
                "dimension_scores": {"medical_safety": 5},
                "verdict": "high",
                "attribution": [],
            },
            "fake",
        )

    item = storage.list_experiences()[0]
    assert item["eligible_for_activation"] is True
    assert storage.apply_experience_action(
        "observe-exp",
        "activate",
        "admin",
    )
    assert storage.list_experiences()[0]["status"] == "active"
