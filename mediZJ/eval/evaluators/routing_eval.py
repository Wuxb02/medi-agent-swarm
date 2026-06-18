"""
指标 1：智能路由准确率评估 (目标 ≥ 95%)

评估方法：
- 对每道题调用 lead_agent.assess_and_decompose() 获取路由决策
- 每题跑 3 次取多数投票
- 比对 expected vs actual：mode + agent 集合
"""
import json
import asyncio
from typing import Dict, Any, List, Tuple
from collections import Counter
from loguru import logger

from mediZJ.eval.config import ROUTING_CASES_PATH, ROUTING_RUNS, THRESHOLDS
from mediZJ.eval.helpers import make_session_id


async def _run_single_routing(coordinator, question: str) -> Dict[str, Any]:
    """执行单次路由评估，只获取路由决策，不执行完整 Agent 流程"""
    # 使用 assess_and_decompose 获取路由决策（更轻量，不需要完整 process）
    assessment = await coordinator.lead_agent.assess_and_decompose(question)
    subtasks = assessment.get("subtasks", [])

    if len(subtasks) == 0:
        return {"mode": "fallback", "agents": ["consultation_agent"]}
    elif len(subtasks) == 1:
        agent_id = subtasks[0].get("assigned_agent", "consultation_agent")
        return {"mode": "single_agent", "agents": [agent_id]}
    else:
        agents = list(set(
            st.get("assigned_agent", "consultation_agent") for st in subtasks
        ))
        return {"mode": "swarm", "agents": agents}


def _vote_routing(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    多数投票：选择出现次数最多的 (mode, frozenset(agents)) 组合
    """
    counter = Counter()
    for r in results:
        key = (r["mode"], frozenset(r["agents"]))
        counter[key] += 1

    best_key, count = counter.most_common(1)[0]
    mode, agents_frozen = best_key
    return {
        "mode": mode,
        "agents": sorted(agents_frozen),
        "vote_count": count,
        "total_runs": len(results),
        "all_results": results
    }


def _compare_routing(expected: Dict, actual: Dict) -> Dict[str, Any]:
    """比对期望路由与实际路由"""
    mode_match = expected["expected_mode"] == actual["mode"]

    expected_set = set(expected["expected_agents"])
    actual_set = set(actual["agents"])

    # 完全匹配
    exact_match = mode_match and (expected_set == actual_set)

    # Agent 召回率：预期 Agent 中有多少被实际选中
    if expected_set:
        recall = len(expected_set & actual_set) / len(expected_set)
    else:
        recall = 1.0

    # Agent 精确率：实际选中 Agent 中有多少是预期的
    if actual_set:
        precision = len(expected_set & actual_set) / len(actual_set)
    else:
        precision = 0.0

    return {
        "mode_match": mode_match,
        "agent_exact_match": exact_match,
        "agent_recall": recall,
        "agent_precision": precision,
        "expected_mode": expected["expected_mode"],
        "actual_mode": actual["mode"],
        "expected_agents": sorted(expected["expected_agents"]),
        "actual_agents": actual["agents"]
    }


async def run_routing_eval(coordinator=None) -> Dict[str, Any]:
    """
    运行路由评估

    Returns:
        评估结果，包含准确率、详细记录等
    """
    from mediZJ.swarm.swarm_coordinator import SwarmCoordinator

    # 加载测试数据
    with open(ROUTING_CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    logger.info(f"路由评估：加载 {len(cases)} 道测试题")

    if coordinator is None:
        coordinator = SwarmCoordinator()

    results = []
    mode_correct = 0
    agent_exact_correct = 0

    for case in cases:
        case_id = case["id"]
        question = case["question"]
        logger.info(f"评估 [{case_id}]: {question[:30]}...")

        # 多次运行取投票
        run_results = []
        for run_i in range(ROUTING_RUNS):
            session_id = make_session_id(f"routing-{case_id}-{run_i}")
            try:
                result = await _run_single_routing(coordinator, question)
                run_results.append(result)
            except Exception as e:
                logger.error(f"  运行 {run_i+1} 失败: {e}")
                run_results.append({"mode": "error", "agents": []})

        voted = _vote_routing(run_results)
        comparison = _compare_routing(case, voted)

        if comparison["mode_match"]:
            mode_correct += 1
        if comparison["agent_exact_match"]:
            agent_exact_correct += 1

        results.append({
            "case_id": case_id,
            "question": question,
            "difficulty": case.get("difficulty", "unknown"),
            "voted_result": voted,
            "comparison": comparison
        })

        logger.info(
            f"  模式={voted['mode']} ({'✓' if comparison['mode_match'] else '✗'}) | "
            f"Agent={voted['agents']} ({'✓' if comparison['agent_exact_match'] else '✗'})"
        )

    total = len(cases)
    mode_accuracy = mode_correct / total if total > 0 else 0
    agent_accuracy = agent_exact_correct / total if total > 0 else 0
    threshold = THRESHOLDS["routing_accuracy"]

    summary = {
        "metric": "routing_accuracy",
        "total_cases": total,
        "mode_accuracy": round(mode_accuracy, 4),
        "agent_exact_accuracy": round(agent_accuracy, 4),
        "threshold": threshold,
        "mode_pass": mode_accuracy >= threshold,
        "agent_pass": agent_accuracy >= threshold,
        "details": results
    }

    logger.info(
        f"\n路由评估结果："
        f"\n  模式准确率: {mode_accuracy:.1%} ({'PASS' if summary['mode_pass'] else 'FAIL'}, 阈值 {threshold:.0%})"
        f"\n  Agent 完全匹配: {agent_accuracy:.1%}"
    )

    return summary
