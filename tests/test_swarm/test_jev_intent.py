"""JEV 意图识别与影子模式的确定性测试。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from mediZJ.eval.intent_cases import build_intent_cases
from mediZJ.eval.intent_comparison import _choose_threshold, _metrics
from mediZJ.swarm.intent_classifier import IntentClassifier


def _answer(choice: str, confidence: float) -> dict:
    return {"answers": {"intent": {"choice": choice, "confidence": confidence}}}


def test_jev_is_default_mode(mock_llm_client, monkeypatch):
    monkeypatch.delenv("INTENT_CLASSIFIER_MODE", raising=False)
    classifier = IntentClassifier(llm_client=mock_llm_client)
    assert classifier.mode == "jev"
    assert classifier.others_threshold == 0.9


@pytest.mark.asyncio
async def test_jev_others_at_threshold(mock_llm_client):
    jev = AsyncMock()
    jev.classify.return_value = _answer("others", 0.9)
    classifier = IntentClassifier(
        llm_client=mock_llm_client,
        mode="jev",
        jev_client=jev,
        others_threshold=0.9,
    )
    result = await classifier.classify("你好")
    assert (result.intent, result.source, result.skip_long_term) == (
        "others", "jev", True
    )


@pytest.mark.asyncio
async def test_jev_low_confidence_stays_medical(mock_llm_client):
    jev = AsyncMock()
    jev.classify.return_value = _answer("others", 0.89)
    result = await IntentClassifier(
        llm_client=mock_llm_client,
        mode="jev",
        jev_client=jev,
        others_threshold=0.9,
    ).classify("你好")
    assert result.intent == "medical"
    assert result.skip_long_term is False


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    _answer("unknown", 0.99),
    _answer("others", float("nan")),
    {"answers": {}},
])
async def test_invalid_jev_result_fails_closed(mock_llm_client, answer):
    jev = AsyncMock()
    jev.classify.return_value = answer
    result = await IntentClassifier(
        llm_client=mock_llm_client, mode="jev", jev_client=jev
    ).classify("你好")
    assert result.intent == "medical"
    assert result.source == "fallback"


@pytest.mark.asyncio
async def test_jev_timeout_fails_closed(mock_llm_client):
    async def slow(*args):
        await asyncio.sleep(1)

    jev = AsyncMock()
    jev.classify.side_effect = slow
    result = await IntentClassifier(
        llm_client=mock_llm_client,
        mode="jev",
        jev_client=jev,
        timeout=0.01,
    ).classify("你好")
    assert result.intent == "medical"
    assert result.source == "fallback"


@pytest.mark.asyncio
async def test_shadow_preserves_baseline_route(mock_llm_client):
    jev = AsyncMock()
    jev.model = "jev-1.13.0"
    jev.classify.return_value = _answer("others", 0.99)
    observations = []
    classifier = IntentClassifier(
        llm_client=mock_llm_client,
        mode="shadow",
        jev_client=jev,
        shadow_allowed=True,
        on_observation=observations.append,
    )
    classifier.classify_llm = AsyncMock(return_value=classifier._fallback("test"))
    result = await classifier.classify("我头痛")
    assert result.intent == "medical"
    assert observations[0]["actual_route"] == "medical"
    assert observations[0]["jev_intent"] == "others"
    assert "question" not in observations[0]


@pytest.mark.asyncio
async def test_shadow_without_permission_does_not_send(mock_llm_client):
    jev = AsyncMock()
    classifier = IntentClassifier(
        llm_client=mock_llm_client, mode="shadow", jev_client=jev
    )
    classifier.classify_llm = AsyncMock(return_value=classifier._fallback("test"))
    await classifier.classify("我头痛")
    jev.classify.assert_not_awaited()


def test_synthetic_cases_are_stratified():
    cases = build_intent_cases()
    assert len(cases) >= 200
    assert {case.split for case in cases} == {"tune", "holdout"}
    for scenario in {case.scenario for case in cases}:
        assert {case.split for case in cases if case.scenario == scenario} == {
            "tune", "holdout"
        }
        for stem_index in range(5):
            group = [
                case for case in cases
                if case.scenario == scenario
                and int(case.case_id.split("-")[-1]) // 4 == stem_index
            ]
            assert len({case.split for case in group}) == 1


def test_threshold_selected_only_from_safe_candidates():
    rows = [
        {
            "expected": expected,
            "intent": intent,
            "confidence": confidence,
            "error": None,
            "latency_ms": 10,
            "input_tokens": 20,
            "output_tokens": 0,
        }
        for expected, intent, confidence in (
            ("medical", "others", 0.75),
            ("others", "others", 0.8),
            ("others", "others", 0.95),
        )
    ]
    threshold = _choose_threshold(rows)
    assert threshold == 0.76
    result = _metrics(rows, threshold)
    assert result["medical_to_others"] == 0
    assert result["others_recall"] == 1.0
