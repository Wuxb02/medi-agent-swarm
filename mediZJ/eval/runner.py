"""
MediZJ Agent Swarm 评估框架 - 统一入口

用法：
    uv run python -m eval.runner --metrics all           # 运行全部评估
    uv run python -m eval.runner --metrics routing        # 仅运行路由评估
    uv run python -m eval.runner --metrics routing,retrieval  # 运行指定指标
    uv run python -m eval.runner --score-abtest           # 计算已有 AB 测试评分结果
"""
import asyncio
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, List

# 确保项目根目录在 path 中
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 加载 .env 环境变量
import dotenv
dotenv.load_dotenv()

from loguru import logger

from mediZJ.eval.config import REPORTS_DIR, THRESHOLDS


# ===== 评估器注册表 =====
EVAL_REGISTRY = {
    "routing": ("mediZJ.eval.evaluators.routing_eval", "run_routing_eval", "智能路由准确率"),
    "retrieval": ("mediZJ.eval.evaluators.retrieval_eval", "run_retrieval_eval", "知识库检索准确率"),
    "latency": ("mediZJ.eval.evaluators.latency_eval", "run_latency_eval", "响应时间"),
    "multiturn": ("mediZJ.eval.evaluators.multiturn_eval", "run_multiturn_eval", "多轮对话上下文理解"),
    "abtest": ("mediZJ.eval.evaluators.abtest_eval", "run_abtest_eval", "AB 测试"),
}


async def _run_evaluations(metrics: List[str], coordinator) -> Dict[str, Any]:
    """执行评估列表，返回结果字典"""
    all_results = {}
    for metric in metrics:
        logger.info(f"\n{'='*50}")
        logger.info(f"开始评估: {EVAL_REGISTRY[metric][2]}")
        logger.info(f"{'='*50}")

        eval_func, _ = _import_evaluator(metric)

        try:
            if metric == "retrieval":
                result = await eval_func()
            else:
                result = await eval_func(coordinator=coordinator)
            all_results[metric] = result
        except Exception as e:
            logger.error(f"评估 {metric} 失败: {e}")
            all_results[metric] = {"error": str(e)}

    return all_results


def _import_evaluator(metric: str):
    """动态导入评估器"""
    module_path, func_name, display_name = EVAL_REGISTRY[metric]
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, func_name), display_name


def _generate_report(all_results: Dict[str, Any]) -> str:
    """生成 Markdown 评估报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# MediZJ Agent Swarm 评估报告",
        f"",
        f"生成时间：{now}",
        f"",
        f"## 概览",
        f"",
        f"| 指标 | 结果 | 阈值 | 状态 |",
        f"|------|------|------|------|",
    ]

    for metric, result in all_results.items():
        if metric == "routing":
            acc = result.get("agent_exact_accuracy", result.get("mode_accuracy", 0))
            threshold = result.get("threshold", 0)
            status = "PASS" if result.get("agent_pass") or result.get("mode_pass") else "FAIL"
            lines.append(f"| 路由准确率 | {acc:.1%} | ≥{threshold:.0%} | {status} |")

        elif metric == "retrieval":
            score = result.get("composite_score", 0)
            threshold = result.get("threshold", 0)
            status = "PASS" if result.get("pass") else "FAIL"
            lines.append(f"| 检索准确率 | {score:.1%} | ≥{threshold:.0%} | {status} |")

        elif metric == "latency":
            single = result.get("single_agent", {})
            swarm = result.get("swarm", {})
            s_status = "PASS" if single.get("pass") else "FAIL"
            w_status = "PASS" if swarm.get("pass") else "FAIL"
            lines.append(f"| 单Agent延迟 (P50) | {single.get('p50', 0)}s | ≤{single.get('threshold_max', 15)}s | {s_status} |")
            lines.append(f"| Swarm延迟 (P50) | {swarm.get('p50', 0)}s | ≤{swarm.get('threshold_max', 30)}s | {w_status} |")

        elif metric == "multiturn":
            acc = result.get("accuracy", 0)
            threshold = result.get("threshold", 0)
            status = "PASS" if result.get("pass") else "FAIL"
            lines.append(f"| 多轮对话准确率 | {acc:.1%} | ≥{threshold:.0%} | {status} |")

        elif metric == "abtest":
            status_text = result.get("status", "awaiting_scoring")
            if status_text == "awaiting_scoring":
                lines.append(f"| AB 测试 | 待评分 | - | - |")
            else:
                score = result.get("system_total", 0)
                threshold = result.get("threshold", 0)
                status = "PASS" if result.get("system_pass") else "FAIL"
                lines.append(f"| AB 测试得分 | {score} | ≥{threshold} | {status} |")

    # 详细结果
    for metric, result in all_results.items():
        _, display_name = _import_evaluator(metric)
        lines.append(f"\n## {display_name}\n")

        if metric == "routing":
            lines.append(f"- 模式准确率: {result.get('mode_accuracy', 0):.1%}")
            lines.append(f"- Agent 完全匹配: {result.get('agent_exact_accuracy', 0):.1%}")
            lines.append(f"- 测试题数: {result.get('total_cases', 0)}")
            lines.append(f"\n### 各题详情\n")
            for d in result.get("details", []):
                c = d.get("comparison", {})
                lines.append(
                    f"- **{d['case_id']}** ({d['difficulty']}): "
                    f"模式={'✓' if c.get('mode_match') else '✗'} "
                    f"Agent={'✓' if c.get('agent_exact_match') else '✗'} "
                   f"→ {d['question'][:30]}"
                )

        elif metric == "retrieval":
            lines.append(f"- Precision@5: {result.get('avg_precision', 0):.1%}")
            lines.append(f"- Recall@5: {result.get('avg_recall', 0):.1%}")
            lines.append(f"- MRR: {result.get('avg_mrr', 0):.1%}")
            lines.append(f"- Hit Rate: {result.get('hit_rate', 0):.1%}")
            lines.append(f"- 综合得分: {result.get('composite_score', 0):.1%}")

        elif metric == "latency":
            for label, key in [("单Agent", "single_agent"), ("Swarm", "swarm")]:
                s = result.get(key, {})
                lines.append(f"\n### {label}\n")
                lines.append(f"- 样本数: {s.get('count', 0)}")
                lines.append(f"- P50: {s.get('p50', 0)}s | P90: {s.get('p90', 0)}s | P95: {s.get('p95', 0)}s")
                lines.append(f"- 超时率: {s.get('timeout_rate', 0):.1%}")

        elif metric == "multiturn":
            lines.append(f"- 总检查项: {result.get('total_checks', 0)}")
            lines.append(f"- 通过项: {result.get('passed_checks', 0)}")
            lines.append(f"- 准确率: {result.get('accuracy', 0):.1%}")

        elif metric == "abtest":
            lines.append(f"- 测试题数: {result.get('total_cases', 0)}")
            lines.append(f"- 盲评文件: `{result.get('blind_review_path', 'N/A')}`")
            lines.append(f"- 状态: {result.get('status', 'unknown')}")

    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description="MediZJ Agent Swarm 评估框架")
    parser.add_argument(
        "--metrics",
        type=str,
        default="all",
        help="要运行的评估指标，逗号分隔。可选: all, routing, retrieval, latency, multiturn, abtest"
    )
    parser.add_argument(
        "--score-abtest",
        action="store_true",
        help="计算已有 AB 测试评分结果"
    )
    args = parser.parse_args()

    # 解析指标列表
    if args.metrics == "all":
        metrics = list(EVAL_REGISTRY.keys())
    else:
        metrics = [m.strip() for m in args.metrics.split(",")]
        for m in metrics:
            if m not in EVAL_REGISTRY:
                logger.error(f"未知指标: {m}，可选: {list(EVAL_REGISTRY.keys())}")
                return

    # AB 测试评分模式
    if args.score_abtest:
        from mediZJ.eval.evaluators.abtest_eval import compute_abtest_scores
        result = await compute_abtest_scores()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    logger.info(f"开始评估，指标: {metrics}")

    # 判断是否需要 coordinator（仅 retrieval 不需要）
    needs_coordinator = any(m != "retrieval" for m in metrics)
    all_results = {}

    if needs_coordinator:
        from mediZJ.eval.helpers import isolated_coordinator
        with isolated_coordinator() as coordinator:
            all_results = await _run_evaluations(metrics, coordinator)
    else:
        all_results = await _run_evaluations(metrics, None)

    # 生成报告
    report = _generate_report(all_results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORTS_DIR, f"eval_report_{timestamp}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info(f"\n评估报告已保存: {report_path}")

    # 保存 JSON 结果
    json_path = os.path.join(REPORTS_DIR, f"eval_result_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)

    # 控制台输出摘要
    print("\n" + "=" * 60)
    print("评估完成")
    print("=" * 60)
    for metric in metrics:
        if metric in all_results and "error" not in all_results[metric]:
            _, display_name = _import_evaluator(metric)
            r = all_results[metric]
            if metric == "routing":
                print(f"  路由准确率: {r.get('agent_exact_accuracy', 0):.1%}")
            elif metric == "retrieval":
                print(f"  检索综合得分: {r.get('composite_score', 0):.1%}")
            elif metric == "latency":
                print(f"  单Agent P50: {r.get('single_agent', {}).get('p50', 0)}s")
                print(f"  Swarm P50: {r.get('swarm', {}).get('p50', 0)}s")
            elif metric == "multiturn":
                print(f"  多轮对话准确率: {r.get('accuracy', 0):.1%}")
            elif metric == "abtest":
                print(f"  AB 测试: {r.get('status', 'unknown')}")
    print(f"\n报告: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
