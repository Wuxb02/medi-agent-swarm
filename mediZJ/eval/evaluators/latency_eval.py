"""
指标 3：响应时间评估 (单 Agent 5-15s, Swarm 20-30s)

评估方法：
- 完整调用 swarm_coordinator.process()，记录端到端耗时
- 按实际路由模式分组统计 P50/P90/P95
- 计算超时率
"""
import json
import time
import asyncio
from typing import Dict, Any, List
import statistics
from loguru import logger

from mediZJ.eval.config import ROUTING_CASES_PATH, LATENCY_RUNS, THRESHOLDS
from mediZJ.eval.helpers import make_session_id, isolated_coordinator


# 用于延迟测试的内置题目
_LATENCY_CASES = [
    # 简单题（预期走单 Agent）
    {"id": "lat_s01", "question": "感冒了怎么办？", "expected_mode": "single_agent"},
    {"id": "lat_s02", "question": "高血压饮食注意什么？", "expected_mode": "single_agent"},
    {"id": "lat_s03", "question": "糖尿病患者能吃水果吗？", "expected_mode": "single_agent"},
    {"id": "lat_s04", "question": "失眠怎么调理？", "expected_mode": "single_agent"},
    {"id": "lat_s05", "question": "过敏性鼻炎怎么预防？", "expected_mode": "single_agent"},
    # 复杂题（预期走 Swarm）
    {"id": "lat_c01", "question": "头痛伴恶心呕吐，严重吗？需要做什么检查？", "expected_mode": "swarm"},
    {"id": "lat_c02", "question": "胸闷心悸，活动后加重，应该怎么处理？", "expected_mode": "swarm"},
    {"id": "lat_c03", "question": "高血压合并糖尿病，最新的治疗方案和饮食建议是什么？", "expected_mode": "swarm"},
]


async def _measure_latency(coordinator, question: str, run_id: str) -> Dict[str, Any]:
    """测量单次请求的端到端延迟"""
    session_id = make_session_id(run_id)
    start = time.perf_counter()
    try:
        result = await coordinator.process(question, session_id=session_id)
        elapsed = time.perf_counter() - start
        return {
            "elapsed_time": round(elapsed, 2),
            "swarm_enabled": result.get("swarm_enabled", False),
            "agents_involved": result.get("agents_involved", []),
            "success": True
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        logger.error(f"  请求失败 ({elapsed:.1f}s): {e}")
        return {
            "elapsed_time": round(elapsed, 2),
            "swarm_enabled": False,
            "agents_involved": [],
            "success": False,
            "error": str(e)
        }


async def run_latency_eval(coordinator=None) -> Dict[str, Any]:
    """运行响应时间评估"""
    logger.info(f"延迟评估：{_LATENCY_CASES.__len__()} 道题目，每题 {LATENCY_RUNS} 次")

    if coordinator is None:
        with isolated_coordinator() as coord:
            return await _run_latency_cases(coord)
    return await _run_latency_cases(coordinator)


async def _run_latency_cases(coordinator) -> Dict[str, Any]:
    """执行延迟测试"""
    single_agent_times = []
    swarm_times = []
    all_results = []

    for case in _LATENCY_CASES:
        case_id = case["id"]
        question = case["question"]
        logger.info(f"延迟测试 [{case_id}]: {question[:30]}...")

        run_times = []
        run_details = []
        for run_i in range(LATENCY_RUNS):
            detail = await _measure_latency(coordinator, question, f"latency-{case_id}-{run_i}")
            run_times.append(detail["elapsed_time"])
            run_details.append(detail)
            if detail["success"]:
                logger.info(f"  运行 {run_i+1}: {detail['elapsed_time']}s (swarm={detail['swarm_enabled']})")

        # 取中位数
        valid_times = [d["elapsed_time"] for d in run_details if d["success"]]
        median_time = statistics.median(valid_times) if valid_times else 0

        # 使用实际路由模式分组
        actual_modes = [d.get("swarm_enabled", False) for d in run_details if d["success"]]
        is_swarm = any(actual_modes) if actual_modes else False

        if is_swarm:
            swarm_times.append(median_time)
        else:
            single_agent_times.append(median_time)

        all_results.append({
            "case_id": case_id,
            "question": question,
            "expected_mode": case["expected_mode"],
            "actual_swarm": is_swarm,
            "median_time": round(median_time, 2),
            "all_runs": run_details
        })

    # 统计分析
    def compute_stats(times: List[float]) -> Dict[str, Any]:
        if not times:
            return {"count": 0, "p50": 0, "p90": 0, "p95": 0, "mean": 0}
        sorted_t = sorted(times)
        n = len(sorted_t)
        return {
            "count": n,
            "p50": round(sorted_t[int(n * 0.5)], 2),
            "p90": round(sorted_t[min(int(n * 0.9), n - 1)], 2),
            "p95": round(sorted_t[min(int(n * 0.95), n - 1)], 2),
            "mean": round(statistics.mean(sorted_t), 2),
            "min": round(min(sorted_t), 2),
            "max": round(max(sorted_t), 2),
        }

    single_stats = compute_stats(single_agent_times)
    swarm_stats = compute_stats(swarm_times)

    # 超时率
    single_timeout = sum(1 for t in single_agent_times if t > THRESHOLDS["single_agent_latency_max"])
    swarm_timeout = sum(1 for t in swarm_times if t > THRESHOLDS["swarm_latency_max"])

    single_timeout_rate = single_timeout / len(single_agent_times) if single_agent_times else 0
    swarm_timeout_rate = swarm_timeout / len(swarm_times) if swarm_times else 0

    single_pass = single_stats["p50"] <= THRESHOLDS["single_agent_latency_max"]
    swarm_pass = swarm_stats["p50"] <= THRESHOLDS["swarm_latency_max"]

    summary = {
        "metric": "latency",
        "single_agent": {
            **single_stats,
            "timeout_rate": round(single_timeout_rate, 4),
            "threshold_max": THRESHOLDS["single_agent_latency_max"],
            "pass": single_pass
        },
        "swarm": {
            **swarm_stats,
            "timeout_rate": round(swarm_timeout_rate, 4),
            "threshold_max": THRESHOLDS["swarm_latency_max"],
            "pass": swarm_pass
        },
        "details": all_results
    }

    logger.info(
        f"\n延迟评估结果："
        f"\n  单Agent: P50={single_stats['p50']}s P90={single_stats['p90']}s "
        f"(阈值 ≤{THRESHOLDS['single_agent_latency_max']}s, {'PASS' if single_pass else 'FAIL'})"
        f"\n  Swarm:   P50={swarm_stats['p50']}s P90={swarm_stats['p90']}s "
        f"(阈值 ≤{THRESHOLDS['swarm_latency_max']}s, {'PASS' if swarm_pass else 'FAIL'})"
    )

    return summary
