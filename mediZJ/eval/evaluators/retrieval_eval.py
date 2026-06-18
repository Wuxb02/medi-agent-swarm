"""
指标 2：知识库检索准确率评估 (目标 ≥ 87%)

评估方法：
- 对每个 query 执行 Milvus 语义检索
- 计算 Precision@K、Recall@K、MRR、Hit Rate
- 综合得分 = 0.4*Recall + 0.3*Precision + 0.3*MRR
"""
import json
from typing import Dict, Any, List
from loguru import logger

from mediZJ.eval.config import RETRIEVAL_CASES_PATH, RETRIEVAL_TOP_K, THRESHOLDS


def _compute_metrics(
    retrieved_doc_ids: List[str],
    expected_doc_ids: List[str],
    top_k: int
) -> Dict[str, float]:
    """计算单个查询的检索指标"""
    retrieved_set = set(retrieved_doc_ids)
    expected_set = set(expected_doc_ids)

    # Hit Rate: 是否至少命中 1 个相关文档
    hit = 1.0 if retrieved_set & expected_set else 0.0

    # Precision@K: 返回结果中相关文档占比
    if retrieved_doc_ids:
        relevant_count = sum(1 for doc_id in retrieved_doc_ids if doc_id in expected_set)
        precision = relevant_count / len(retrieved_doc_ids)
    else:
        precision = 0.0

    # Recall@K: 相关文档被检索到的比例
    if expected_doc_ids:
        recall = len(retrieved_set & expected_set) / len(expected_set)
    else:
        recall = 1.0

    # MRR: 第一个相关文档的排名倒数
    mrr = 0.0
    for rank, doc_id in enumerate(retrieved_doc_ids, 1):
        if doc_id in expected_set:
            mrr = 1.0 / rank
            break

    return {
        "hit": hit,
        "precision": precision,
        "recall": recall,
        "mrr": mrr
    }


async def run_retrieval_eval() -> Dict[str, Any]:
    """
    运行检索评估

    直接调用 MedicalKnowledgeBase.search()，不经过 Agent 流程
    """
    from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase

    # 加载测试数据
    with open(RETRIEVAL_CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    logger.info(f"检索评估：加载 {len(cases)} 个查询")

    kb = MedicalKnowledgeBase()

    results = []
    total_precision = 0.0
    total_recall = 0.0
    total_mrr = 0.0
    total_hit = 0.0

    for case in cases:
        case_id = case["id"]
        query = case["query"]
        expected_doc_ids = case["expected_doc_ids"]

        logger.info(f"评估 [{case_id}]: {query[:30]}...")

        try:
            search_results = kb.search(query, top_k=RETRIEVAL_TOP_K)
            # 提取检索到的文档 ID（从 metadata 中提取）
            retrieved_doc_ids = []
            for r in search_results:
                metadata = r.get("metadata", {})
                doc_id = metadata.get("doc_id", "") if isinstance(metadata, dict) else ""
                if doc_id and doc_id not in retrieved_doc_ids:
                    retrieved_doc_ids.append(doc_id)
        except Exception as e:
            logger.error(f"  检索失败: {e}")
            retrieved_doc_ids = []

        metrics = _compute_metrics(retrieved_doc_ids, expected_doc_ids, RETRIEVAL_TOP_K)

        total_precision += metrics["precision"]
        total_recall += metrics["recall"]
        total_mrr += metrics["mrr"]
        total_hit += metrics["hit"]

        results.append({
            "case_id": case_id,
            "query": query,
            "expected_doc_ids": expected_doc_ids,
            "retrieved_doc_ids": retrieved_doc_ids,
            "metrics": metrics
        })

        logger.info(
            f"  Hit={'✓' if metrics['hit'] else '✗'} | "
            f"P={metrics['precision']:.2f} R={metrics['recall']:.2f} MRR={metrics['mrr']:.2f}"
        )

    n = len(cases) if cases else 1
    avg_precision = total_precision / n
    avg_recall = total_recall / n
    avg_mrr = total_mrr / n
    avg_hit_rate = total_hit / n

    # 综合得分
    composite_score = 0.4 * avg_recall + 0.3 * avg_precision + 0.3 * avg_mrr

    threshold = THRESHOLDS["retrieval_accuracy"]

    summary = {
        "metric": "retrieval_accuracy",
        "total_cases": len(cases),
        "avg_precision": round(avg_precision, 4),
        "avg_recall": round(avg_recall, 4),
        "avg_mrr": round(avg_mrr, 4),
        "hit_rate": round(avg_hit_rate, 4),
        "composite_score": round(composite_score, 4),
        "threshold": threshold,
        "pass": composite_score >= threshold,
        "details": results
    }

    logger.info(
        f"\n检索评估结果："
        f"\n  Precision@{RETRIEVAL_TOP_K}: {avg_precision:.1%}"
        f"\n  Recall@{RETRIEVAL_TOP_K}: {avg_recall:.1%}"
        f"\n  MRR: {avg_mrr:.1%}"
        f"\n  Hit Rate: {avg_hit_rate:.1%}"
        f"\n  综合得分: {composite_score:.1%} ({'PASS' if summary['pass'] else 'FAIL'}, 阈值 {threshold:.0%})"
    )

    return summary
