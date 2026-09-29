"""用合成中文问句对照现有 LLM 与 JEV 意图识别。"""

import argparse
import asyncio
import json
import math
import os
import statistics
import time
from collections import defaultdict
from typing import Any

from dotenv import load_dotenv

from mediZJ.core.llm_client import LLMClient
from mediZJ.core.prompt_loader import PromptLoader
from mediZJ.memory.prompt_prefix import PromptPrefixAssembler
from mediZJ.swarm.intent_classifier import JevIntentClient

from .intent_cases import IntentCase, build_intent_cases


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.ceil((len(ordered) - 1) * fraction)]


def _metrics(rows: list[dict[str, Any]], threshold: float | None = None) -> dict:
    confusion = {
        expected: {actual: 0 for actual in ("medical", "others")}
        for expected in ("medical", "others")
    }
    latencies = []
    failures = 0
    timeouts = 0
    input_tokens = 0
    output_tokens = 0
    for row in rows:
        actual = row["intent"]
        if threshold is not None and actual == "others":
            if row["confidence"] < threshold:
                actual = "medical"
        confusion[row["expected"]][actual] += 1
        latencies.append(row["latency_ms"])
        failures += bool(row["error"])
        timeouts += row["error"] == "TimeoutError"
        input_tokens += row["input_tokens"]
        output_tokens += row["output_tokens"]
    others_total = sum(confusion["others"].values())
    return {
        "n": len(rows),
        "confusion": confusion,
        "medical_to_others": confusion["medical"]["others"],
        "others_recall": (
            confusion["others"]["others"] / others_total if others_total else 0
        ),
        "failure_rate": failures / len(rows) if rows else 0,
        "timeout_rate": timeouts / len(rows) if rows else 0,
        "p50_ms": round(statistics.median(latencies), 2) if latencies else 0,
        "p95_ms": round(_percentile(latencies, 0.95), 2),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


async def _llm_call(client: LLMClient, case: IntentCase) -> dict[str, Any]:
    prompt = PromptLoader.render("memory/intent_gate.j2", question=case.question)
    started = time.perf_counter()
    error = None
    usage = None
    try:
        response = await asyncio.wait_for(
            client.client.chat.completions.create(
                model=client.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": PromptPrefixAssembler.global_prefix(
                            "你是医疗助手的意图识别模块，仅输出 JSON。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=client.max_tokens,
                response_format={"type": "json_object"},
            ),
            timeout=3.0,
        )
        raw = json.loads(response.choices[0].message.content or "")
        intent = raw["intent"]
        if intent not in {"medical", "others"}:
            raise ValueError("invalid intent")
        confidence = float(raw.get("confidence", 0))
        usage = response.usage
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        error = f"{type(exc).__name__}:{status}" if status else type(exc).__name__
        intent, confidence = "medical", 0.0
    return {
        "intent": intent,
        "confidence": confidence,
        "error": error,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


async def _jev_call(client: JevIntentClient, case: IntentCase) -> dict[str, Any]:
    started = time.perf_counter()
    error = None
    usage = {}
    try:
        response = await asyncio.wait_for(client.classify(case.question, 3.0), 3.0)
        answer = response["answers"]["intent"]
        intent = answer["choice"]
        confidence = float(answer["confidence"])
        if intent not in {"medical", "others"} or not 0 <= confidence <= 1:
            raise ValueError("invalid answer")
        usage = response.get("usage") or {}
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        error = f"{type(exc).__name__}:{status}" if status else type(exc).__name__
        intent, confidence = "medical", 0.0
    return {
        "intent": intent,
        "confidence": confidence,
        "error": error,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
    }


def _choose_threshold(rows: list[dict[str, Any]]) -> float:
    candidates = [round(index / 100, 2) for index in range(50, 101)]
    safe = [
        value for value in candidates
        if _metrics(rows, value)["medical_to_others"] == 0
    ]
    if not safe:
        return 1.0
    return max(safe, key=lambda value: (_metrics(rows, value)["others_recall"], -value))


async def run_comparison() -> dict[str, Any]:
    load_dotenv()
    if not os.getenv("TYPESAFE_API_KEY"):
        raise ValueError("需要 TYPESAFE_API_KEY 才能运行真实 JEV 对照")
    llm = LLMClient()
    jev = JevIntentClient(
        os.environ["TYPESAFE_API_KEY"], os.getenv("JEV_MODEL", "jev-1.13.0")
    )
    rows = {"llm": [], "jev": []}
    cases = build_intent_cases()
    try:
        probe = cases[0]
        llm_probe, jev_probe = await asyncio.gather(
            _llm_call(llm, probe), _jev_call(jev, probe)
        )
        errors = {
            name: result["error"]
            for name, result in (("llm", llm_probe), ("jev", jev_probe))
            if result["error"]
        }
        if errors:
            raise RuntimeError(f"意图对照预检失败：{errors}")
        semaphore = asyncio.Semaphore(12)

        async def measure(name, client, call, case, repeat):
            async with semaphore:
                result = await call(client, case)
            return name, {
                **result,
                "case_id": case.case_id,
                "expected": case.expected,
                "scenario": case.scenario,
                "split": case.split,
                "repeat": repeat,
            }

        tasks = [
            measure(name, client, call, case, repeat)
            for case in cases
            for repeat in range(3)
            for name, client, call in (
                ("llm", llm, _llm_call),
                ("jev", jev, _jev_call),
            )
        ]
        for name, result in await asyncio.gather(*tasks):
            rows[name].append(result)
    finally:
        await jev.close()
    tune = [row for row in rows["jev"] if row["split"] == "tune"]
    threshold = _choose_threshold(tune)
    report = {
        "cases": len(cases),
        "repeats": 3,
        "jev_model": jev.model,
        "llm_model": llm.model_name,
        "selected_threshold": threshold,
        "splits": {},
        "limitations": (
            "人工合成中文样本不能代表真实流量；零误判不证明医疗安全。"
        ),
    }
    for split in ("tune", "holdout"):
        report["splits"][split] = {}
        for name in ("llm", "jev"):
            subset = [row for row in rows[name] if row["split"] == split]
            gate = threshold if name == "jev" else None
            scenarios = defaultdict(list)
            for row in subset:
                scenarios[row["scenario"]].append(row)
            report["splits"][split][name] = {
                **_metrics(subset, gate),
                "scenarios": {
                    key: _metrics(value, gate)
                    for key, value in scenarios.items()
                },
            }
            metrics = report["splits"][split][name]
            if name == "jev":
                metrics["estimated_cost_usd"] = round(
                    metrics["input_tokens"] * 0.042 / 1_000_000, 6
                )
            else:
                input_price = os.getenv("INTENT_LLM_INPUT_USD_PER_M")
                output_price = os.getenv("INTENT_LLM_OUTPUT_USD_PER_M")
                metrics["estimated_cost_usd"] = (
                    round(
                        (
                            metrics["input_tokens"] * float(input_price)
                            + metrics["output_tokens"] * float(output_price)
                        ) / 1_000_000,
                        6,
                    )
                    if input_price and output_price else None
                )
    holdout = report["splits"]["holdout"]
    llm_result, jev_result = holdout["llm"], holdout["jev"]
    latency_better = jev_result["p95_ms"] < llm_result["p95_ms"]
    cost_known = llm_result["estimated_cost_usd"] is not None
    cost_better = (
        cost_known
        and jev_result["estimated_cost_usd"] < llm_result["estimated_cost_usd"]
    )
    latency_acceptable = jev_result["p95_ms"] <= llm_result["p95_ms"] * 1.05
    cost_acceptable = (
        not cost_known
        or jev_result["estimated_cost_usd"]
        <= llm_result["estimated_cost_usd"] * 1.05
    )
    report["switch_review_pass"] = (
        jev_result["medical_to_others"] == 0
        and jev_result["others_recall"] >= llm_result["others_recall"]
        and (latency_better or cost_better)
        and latency_acceptable
        and cost_acceptable
        and jev_result["failure_rate"] <= llm_result["failure_rate"]
    )
    report["switch_review_note"] = (
        "JEV 按官方当前输入价估算；LLM 单价未配置时仅用延迟判断收益。"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="中文合成问句意图识别对照")
    parser.parse_args()
    report = asyncio.run(run_comparison())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
