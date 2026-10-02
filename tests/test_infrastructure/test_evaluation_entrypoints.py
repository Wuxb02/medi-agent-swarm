"""验证异步存储改造后的评测入口，模型与报告文件使用边界替身。"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, mock_open

import pytest

from mediZJ.eval import runner
from mediZJ.eval.evaluators import abtest_eval, latency_eval, multiturn_eval
from mediZJ.eval.evaluators import retrieval_eval


def input_file(monkeypatch, module, value):
    """报告只写入内存替身，不产生评测文件。"""
    handle = mock_open(read_data=json.dumps(value))
    monkeypatch.setattr(module, "open", handle, raising=False)
    return handle


async def test_retrieval_deduplicates_documents_and_counts_failures(monkeypatch):
    input_file(
        monkeypatch,
        retrieval_eval,
        [
            {"id": "good", "query": "健康", "expected_doc_ids": ["doc"]},
            {"id": "failed", "query": "失败", "expected_doc_ids": ["doc"]},
        ],
    )
    kb = MagicMock()
    kb.search = AsyncMock(
        side_effect=[
            [
                {"metadata": {"doc_id": "doc"}},
                {"metadata": {"doc_id": "doc"}},
                {"metadata": "invalid"},
            ],
            ConnectionError("断线"),
        ]
    )
    monkeypatch.setattr("mediZJ.knowledge.milvus_kb.MedicalKnowledgeBase", lambda: kb)
    result = await retrieval_eval.run_retrieval_eval()
    assert result["composite_score"] == 0.5
    assert result["details"][0]["retrieved_doc_ids"] == ["doc"]
    assert result["details"][1]["metrics"]["hit"] == 0
    assert retrieval_eval._compute_metrics([], [], 5)["recall"] == 1


async def test_multiturn_keeps_session_and_counts_missing_context(monkeypatch):
    input_file(
        monkeypatch,
        multiturn_eval,
        [
            {
                "id": "case",
                "turns": [
                    {"content": "第一轮", "expect_context_keywords": ["高血压"]},
                    {"content": "追问", "expect_context_keywords": ["高血压"]},
                ],
            }
        ],
    )
    coordinator = MagicMock()
    coordinator.process = AsyncMock(
        side_effect=[
            {"answer": "高血压建议"},
            RuntimeError("模型失败"),
        ]
    )
    result = await multiturn_eval.run_multiturn_eval(coordinator)
    assert result["accuracy"] == 0.5
    calls = coordinator.process.await_args_list
    assert calls[0].kwargs["session_id"] == calls[1].kwargs["session_id"]
    assert multiturn_eval._check_keywords("", [])["hit"]
    client = MagicMock(
        chat=AsyncMock(
            side_effect=[
                "评分：9\n理由：良好",
                "无法评分",
                RuntimeError("失败"),
            ]
        )
    )
    scores = [
        await multiturn_eval._llm_judge_context(client, "", "", "", "")
        for _ in range(3)
    ]
    assert [value["score"] for value in scores] == [5, 3, 3]


async def test_latency_uses_actual_routing_and_reports_call_failure(monkeypatch):
    monkeypatch.setattr(latency_eval, "LATENCY_RUNS", 1)
    monkeypatch.setattr(
        latency_eval,
        "_LATENCY_CASES",
        [
            {"id": "single", "question": "简单", "expected_mode": "single_agent"},
            {"id": "swarm", "question": "复杂", "expected_mode": "swarm"},
        ],
    )
    coordinator = MagicMock(
        process=AsyncMock(
            side_effect=[
                {"swarm_enabled": False},
                {"swarm_enabled": True},
                RuntimeError("失败"),
            ]
        )
    )
    result = await latency_eval.run_latency_eval(coordinator)
    assert result["single_agent"]["count"] == 1
    assert result["swarm"]["count"] == 1
    failed = await latency_eval._measure_latency(coordinator, "问题", "failure")
    assert failed["success"] is False


@pytest.mark.parametrize(
    "module,name",
    [
        (latency_eval, "run_latency_eval"),
        (abtest_eval, "run_abtest_eval"),
        (multiturn_eval, "run_multiturn_eval"),
    ],
)
async def test_evaluators_enter_async_isolation(monkeypatch, module, name):
    input_file(monkeypatch, module, [])
    coordinator = MagicMock()
    entered = []

    @asynccontextmanager
    async def isolate():
        entered.append(coordinator)
        yield coordinator

    monkeypatch.setattr(module, "isolated_coordinator", isolate)
    if module is latency_eval:
        monkeypatch.setattr(module, "_LATENCY_CASES", [])
    if module is multiturn_eval:
        input_file(monkeypatch, module, [{"id": "case", "turns": []}])
    await getattr(module, name)()
    assert entered == [coordinator]


async def test_abtest_blinding_and_score_assignment(monkeypatch):
    input_file(
        monkeypatch,
        abtest_eval,
        [
            {"id": "success", "question": "一"},
            {"id": "failure", "question": "二"},
        ],
    )
    coordinator = MagicMock(
        process=AsyncMock(
            side_effect=[
                {"answer": "系统", "swarm_enabled": True},
                RuntimeError("失败"),
            ]
        )
    )
    monkeypatch.setattr(
        abtest_eval, "_get_baseline_answer", AsyncMock(return_value="基线")
    )
    monkeypatch.setattr(abtest_eval.random, "random", MagicMock(side_effect=[0.9, 0.1]))
    result = await abtest_eval.run_abtest_eval(coordinator)
    assert result["details"][0]["answer_A"] == "系统"
    assert result["details"][1]["answer_A"] == "基线"
    assert result["details"][1]["system_mode"] == "error"
    scores = {"accuracy": 5, "completeness": 5, "safety": 5}
    low = {"accuracy": 3, "completeness": 3, "safety": 3}
    input_file(
        monkeypatch,
        abtest_eval,
        [
            {"is_system_A": True, "scores": {"A": scores, "B": low}},
            {"is_system_A": False, "scores": {"A": low, "B": scores}},
            {"scores": {}},
        ],
    )
    scored = await abtest_eval.compute_abtest_scores()
    assert scored["scored_count"] == 2
    assert scored["system_total"] == 5
    assert scored["improvement"] == 2
    input_file(monkeypatch, abtest_eval, [])
    assert (await abtest_eval.compute_abtest_scores())["status"] == "no_scores"


async def test_registry_keeps_failure_and_builds_report(monkeypatch):
    evaluate = AsyncMock(side_effect=[{"accuracy": 1}, RuntimeError("失败")])
    monkeypatch.setattr(runner, "_import_evaluator", lambda metric: (evaluate, metric))
    result = await runner._run_evaluations(["retrieval", "multiturn"], MagicMock())
    assert "error" in result["multiturn"]
    assert evaluate.await_args_list[0].kwargs == {}
    report = runner._generate_report(
        {
            "routing": {
                "details": [
                    {
                        "case_id": "one",
                        "difficulty": "easy",
                        "question": "问题",
                        "comparison": {},
                    }
                ]
            },
            "retrieval": {},
            "latency": {},
            "multiturn": {},
            "abtest": {},
        }
    )
    assert "检索准确率" in report
    assert "待评分" in report
    assert "AB 测试得分" in runner._generate_report({"abtest": {"status": "scored"}})


@pytest.mark.parametrize("failure", [False, True])
async def test_baseline_restores_absent_and_empty_environment(monkeypatch, failure):
    import os

    for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setattr("dotenv.load_dotenv", lambda: None)
    client = MagicMock(
        chat=AsyncMock(
            return_value="基线", side_effect=RuntimeError("失败") if failure else None
        )
    )
    monkeypatch.setattr("mediZJ.core.llm_client.LLMClient", lambda: client)
    result = await abtest_eval._get_baseline_answer("问题")
    assert "失败" in result if failure else result == "基线"
    assert "LLM_API_KEY" not in os.environ
    assert "LLM_MODEL_NAME" not in os.environ
    assert os.environ["LLM_BASE_URL"] == ""


@pytest.mark.parametrize("metrics", ["retrieval", "routing", "all", "unknown"])
async def test_runner_handles_metrics_and_async_context(monkeypatch, metrics, capsys):
    import sys
    from mediZJ.infrastructure import database, redis_client

    runtime = {}
    for module, names in (
        (
            database,
            ["initialize_database", "validate_runtime_schema", "close_database"],
        ),
        (redis_client, ["initialize_redis", "close_redis"]),
    ):
        for name in names:
            runtime[name] = AsyncMock()
            monkeypatch.setattr(module, name, runtime[name])
    monkeypatch.setattr(sys, "argv", ["evaluation", "--metrics", metrics])
    input_file(monkeypatch, runner, [])
    monkeypatch.setattr(
        runner, "_import_evaluator", lambda metric: (AsyncMock(return_value={}), metric)
    )
    entered = []

    @asynccontextmanager
    async def isolate():
        entered.append(True)
        yield MagicMock()

    monkeypatch.setattr("mediZJ.eval.helpers.isolated_coordinator", isolate)
    await runner.main()
    if metrics in {"routing", "all"}:
        assert entered == [True]
    else:
        assert entered == []
    if metrics != "unknown":
        assert "评估完成" in capsys.readouterr().out
        for call in runtime.values():
            call.assert_awaited_once()


async def test_runner_scoring_uses_saved_scores_only(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["evaluation", "--score-abtest"])
    scores = AsyncMock(return_value={"system_total": 5})
    monkeypatch.setattr(abtest_eval, "compute_abtest_scores", scores)
    await runner.main()
    scores.assert_awaited_once()
    assert "system_total" in capsys.readouterr().out
