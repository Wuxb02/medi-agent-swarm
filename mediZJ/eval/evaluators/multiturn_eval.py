"""
指标 4：多轮对话上下文理解准确率评估 (目标 ≥ 92%)

评估方法：
- 对每组多轮对话，使用同一 session_id 顺序调用 process()
- 每轮检查：关键词命中 + LLM-as-Judge 评分
- 综合准确率 = (关键词命中 + LLM评分≥4) / 总检查项
"""
import json
import re
from typing import Dict, Any, List
from loguru import logger

from mediZJ.eval.config import MULTITURN_CASES_PATH, THRESHOLDS
from mediZJ.eval.helpers import make_session_id, isolated_coordinator


def _check_keywords(answer: str, expected_keywords: List[str]) -> Dict[str, Any]:
    """检查回答中是否包含期望的上下文关键词"""
    if not expected_keywords:
        return {"hit": True, "matched": [], "missing": [], "hit_rate": 1.0}

    matched = []
    missing = []
    for kw in expected_keywords:
        if kw in answer:
            matched.append(kw)
        else:
            missing.append(kw)

    hit_rate = len(matched) / len(expected_keywords) if expected_keywords else 1.0
    return {
        "hit": len(missing) == 0,
        "matched": matched,
        "missing": missing,
        "hit_rate": hit_rate
    }


async def _llm_judge_context(
    llm_client,
    history: str,
    current_question: str,
    answer: str,
    expected_context: str
) -> Dict[str, Any]:
    """使用 LLM-as-Judge 评估上下文理解质量"""
    prompt = f"""你是一个医学对话评估专家。请评估以下回答是否正确理解了对话上下文。

前文对话：
{history}

当前追问：{current_question}

系统回答：
{answer}

期望关联：{expected_context}

请严格按以下格式输出：
评分：<1-5的整数>
理由：<一句话说明>

评分标准：
5 - 完美理解上下文，准确关联前文信息
4 - 正确理解上下文，基本关联前文信息
3 - 部分理解上下文，遗漏一些前文信息
2 - 理解有偏差，未正确关联前文
1 - 完全未理解上下文"""

    try:
        response = await llm_client.chat([{"role": "user", "content": prompt}])

        # 解析评分
        score_match = re.search(r'评分[：:]\s*(\d)', response)
        score = int(score_match.group(1)) if score_match else 3

        reason_match = re.search(r'理由[：:]\s*(.+)', response)
        reason = reason_match.group(1).strip() if reason_match else "无法解析"

        return {"score": max(1, min(5, score)), "reason": reason}
    except Exception as e:
        logger.error(f"LLM Judge 评分失败: {e}")
        return {"score": 3, "reason": f"评分失败: {e}"}


async def run_multiturn_eval(coordinator=None) -> Dict[str, Any]:
    """运行多轮对话上下文理解评估"""
    from mediZJ.swarm.swarm_coordinator import SwarmCoordinator

    with open(MULTITURN_CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)

    logger.info(f"多轮对话评估：加载 {len(cases)} 组对话")

    results = []
    total_checks = 0
    passed_checks = 0

    for case in cases:
        case_id = case["id"]
        turns = case["turns"]
        session_id = make_session_id(f"multiturn-{case_id}")

        logger.info(f"\n评估 [{case_id}]: {case.get('description', '')}")

        # 每组对话使用独立 coordinator，但共享 session_id
        if coordinator is None:
            with isolated_coordinator() as coord:
                case_result = await _run_multiturn_case(
                    coord, case_id, turns, session_id
                )
        else:
            case_result = await _run_multiturn_case(
                coordinator, case_id, turns, session_id
            )

        results.append(case_result)
        total_checks += case_result["total_checks"]
        passed_checks += case_result["passed_checks"]

    accuracy = passed_checks / total_checks if total_checks > 0 else 0
    threshold = THRESHOLDS["multiturn_accuracy"]

    summary = {
        "metric": "multiturn_context_accuracy",
        "total_cases": len(cases),
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "accuracy": round(accuracy, 4),
        "threshold": threshold,
        "pass": accuracy >= threshold,
        "details": results
    }

    logger.info(
        f"\n多轮对话评估结果："
        f"\n  准确率: {accuracy:.1%} ({'PASS' if summary['pass'] else 'FAIL'}, 阈值 {threshold:.0%})"
        f"\n  通过: {passed_checks}/{total_checks}"
    )

    return summary


async def _run_multiturn_case(
    coordinator,
    case_id: str,
    turns: List[Dict],
    session_id: str
) -> Dict[str, Any]:
    """执行单组多轮对话评估"""
    turn_results = []
    total_checks = 0
    passed_checks = 0
    conversation_history = ""

    for i, turn in enumerate(turns):
        question = turn["content"]
        expect_keywords = turn.get("expect_context_keywords", [])

        logger.info(f"  轮次 {i+1}: {question[:30]}...")

        try:
            result = await coordinator.process(
                question=question,
                session_id=session_id
            )
            answer = result.get("answer", "")
        except Exception as e:
            logger.error(f"  轮次 {i+1} 失败: {e}")
            answer = f"[ERROR: {e}]"

        # 更新对话历史
        conversation_history += f"\n用户：{question}\n助手：{answer[:200]}...\n"

        # 检查关键词命中
        kw_check = _check_keywords(answer, expect_keywords)

        turn_passed = kw_check["hit"]
        if turn_passed:
            passed_checks += 1
        total_checks += 1

        turn_results.append({
            "turn_index": i,
            "question": question,
            "answer_preview": answer[:200],
            "keyword_check": kw_check,
            "passed": turn_passed
        })

        logger.info(
            f"    关键词: {kw_check['matched']}/{expect_keywords} "
            f"({'✓' if kw_check['hit'] else '✗'})"
        )

    return {
        "case_id": case_id,
        "turn_results": turn_results,
        "total_checks": total_checks,
        "passed_checks": passed_checks
    }
