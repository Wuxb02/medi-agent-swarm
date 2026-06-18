"""
指标 5：整体回答质量 AB Test (本系统 4.5 vs Baseline 3.9)

评估方法：
- 对每道题：本系统 vs Baseline（同模型无包装）
- A/B 随机分配，生成盲评数据
- 输出 JSON 供人工/LLM 三维度评分
"""
import json
import random
import os
from typing import Dict, Any, List
from loguru import logger

from mediZJ.eval.config import ABTEST_CASES_PATH, REPORTS_DIR
from mediZJ.eval.config import (
    BASELINE_LLM_API_KEY,
    BASELINE_LLM_BASE_URL,
    BASELINE_LLM_MODEL_NAME,
    THRESHOLDS
)
from mediZJ.eval.helpers import make_session_id, isolated_coordinator


async def _get_baseline_answer(question: str) -> str:
    """获取 Baseline 回答（同模型无包装）"""
    from mediZJ.core.llm_client import LLMClient
    import dotenv
    dotenv.load_dotenv()

    # 使用独立配置的 Baseline LLM
    original_key = os.environ.get("LLM_API_KEY")
    original_url = os.environ.get("LLM_BASE_URL")
    original_model = os.environ.get("LLM_MODEL_NAME")

    try:
        # 临时切换环境变量
        os.environ["LLM_API_KEY"] = BASELINE_LLM_API_KEY or ""
        os.environ["LLM_BASE_URL"] = BASELINE_LLM_BASE_URL or ""
        os.environ["LLM_MODEL_NAME"] = BASELINE_LLM_MODEL_NAME

        client = LLMClient()
        response = await client.chat([
            {
                "role": "system",
                "content": "你是一个医学健康助手，请回答用户的健康问题。请注意你不能提供诊断或处方，建议用户咨询专业医生。"
            },
            {"role": "user", "content": question}
        ])
        return response
    except Exception as e:
        logger.error(f"Baseline 调用失败: {e}")
        return f"[Baseline 调用失败: {e}]"
    finally:
        # 恢复原始配置
        if original_key:
            os.environ["LLM_API_KEY"] = original_key
        if original_url:
            os.environ["LLM_BASE_URL"] = original_url
        if original_model:
            os.environ["LLM_MODEL_NAME"] = original_model


async def run_abtest_eval(coordinator=None) -> Dict[str, Any]:
    """
    运行 AB 测试评估

    生成盲评数据文件，不进行自动评分
    """
    from mediZJ.swarm.swarm_coordinator import SwarmCoordinator

    with open(ABTEST_CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    logger.info(f"AB 测试评估：加载 {len(cases)} 道题目")

    if coordinator is None:
        with isolated_coordinator() as coord:
            return await _run_abtest_cases(coord, cases)
    return await _run_abtest_cases(coordinator, cases)


async def _run_abtest_cases(coordinator, cases: List[Dict]) -> Dict[str, Any]:
    """执行 AB 测试用例"""
    blind_data = []

    for case in cases:
        case_id = case["id"]
        question = case["question"]
        logger.info(f"AB 测试 [{case_id}]: {question[:30]}...")

        # 获取本系统回答
        session_id = make_session_id(f"abtest-{case_id}")
        try:
            system_result = await coordinator.process(
                question=question,
                session_id=session_id
            )
            system_answer = system_result.get("answer", "")
            system_time = system_result.get("total_time", 0)
            system_mode = "swarm" if system_result.get("swarm_enabled") else "single_agent"
        except Exception as e:
            logger.error(f"  系统调用失败: {e}")
            system_answer = f"[系统调用失败: {e}]"
            system_time = 0
            system_mode = "error"

        # 获取 Baseline 回答
        baseline_answer = await _get_baseline_answer(question)

        # A/B 随机分配
        is_system_a = random.random() > 0.5
        if is_system_a:
            answer_a, answer_b = system_answer, baseline_answer
        else:
            answer_a, answer_b = baseline_answer, system_answer

        blind_data.append({
            "case_id": case_id,
            "question": question,
            "difficulty": case.get("difficulty", "unknown"),
            "category": case.get("category", "unknown"),
            "dimensions": case.get("dimensions", ["accuracy", "completeness", "safety"]),
            "answer_A": answer_a,
            "answer_B": answer_b,
            "is_system_A": is_system_a,
            "system_mode": system_mode,
            "system_time": system_time,
            # 评分模板（留空，待人工/LLM 填写）
            "scores": {
                "A": {"accuracy": None, "completeness": None, "safety": None},
                "B": {"accuracy": None, "completeness": None, "safety": None}
            }
        })

        logger.info(f"  系统={system_mode} ({system_time:.1f}s) | A/B={'System=A' if is_system_a else 'System=B'}")

    # 保存盲评数据
    output_path = os.path.join(REPORTS_DIR, "abtest_blind_review.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(blind_data, f, ensure_ascii=False, indent=2)

    logger.info(f"\n盲评数据已保存: {output_path}")
    logger.info("请使用 abtest_score.py 进行人工/LLM 评分")

    return {
        "metric": "abtest",
        "total_cases": len(cases),
        "blind_review_path": output_path,
        "status": "awaiting_scoring",
        "details": blind_data
    }


async def compute_abtest_scores(blind_review_path: str = None) -> Dict[str, Any]:
    """
    从已评分的盲评数据中计算 AB 测试结果

    需要先通过人工或 LLM 填写 scores 字段
    """
    if blind_review_path is None:
        blind_review_path = os.path.join(REPORTS_DIR, "abtest_blind_review.json")

    with open(blind_review_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    system_scores = {"accuracy": [], "completeness": [], "safety": []}
    baseline_scores = {"accuracy": [], "completeness": [], "safety": []}

    scored_count = 0
    for item in data:
        scores = item.get("scores", {})
        is_system_a = item.get("is_system_A", True)

        a_scores = scores.get("A", {})
        b_scores = scores.get("B", {})

        # 跳过未评分的
        if a_scores.get("accuracy") is None or b_scores.get("accuracy") is None:
            continue

        scored_count += 1
        for dim in ["accuracy", "completeness", "safety"]:
            if is_system_a:
                system_scores[dim].append(a_scores[dim])
                baseline_scores[dim].append(b_scores[dim])
            else:
                system_scores[dim].append(b_scores[dim])
                baseline_scores[dim].append(a_scores[dim])

    if scored_count == 0:
        return {"status": "no_scores", "message": "暂无已评分数据"}

    # 计算均值
    def avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else 0

    system_avg = {k: avg(v) for k, v in system_scores.items()}
    baseline_avg = {k: avg(v) for k, v in baseline_scores.items()}

    system_total = avg([v for vs in system_scores.values() for v in vs])
    baseline_total = avg([v for vs in baseline_scores.values() for v in vs])

    threshold = THRESHOLDS["abtest_system_score"]

    return {
        "metric": "abtest_scored",
        "scored_count": scored_count,
        "system_avg": system_avg,
        "system_total": system_total,
        "baseline_avg": baseline_avg,
        "baseline_total": baseline_total,
        "system_pass": system_total >= threshold,
        "threshold": threshold,
        "improvement": round(system_total - baseline_total, 2)
    }
