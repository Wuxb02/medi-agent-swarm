"""
Assess Risk Skill
风险评估 Skill（依赖 RAG 知识库）
"""
from typing import Dict, Any, List
from loguru import logger

# 全局知识库实例（避免重复加载模型）
_kb_instance = None


def get_knowledge_base():
    """获取知识库单例"""
    global _kb_instance
    if _kb_instance is None:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        _kb_instance = MedicalKnowledgeBase()
    return _kb_instance


async def assess_risk(symptoms: str) -> Dict[str, Any]:
    """
    评估症状风险等级

    Args:
        symptoms: 症状描述（字符串）

    Returns:
        {
            "answer": "格式化的风险评估结果",
            "risk_level": "low/medium/high/emergency",
            "recommendation": "就医建议"
        }
    """
    logger.info(f"Assessing risk: symptoms={symptoms}")

    # 将症状字符串转换为列表
    symptom_list = [s.strip() for s in symptoms.split(",") if s.strip()]
    if not symptom_list:
        symptom_list = [symptoms]

    # 高风险症状列表
    high_risk_symptoms = [
        "胸痛", "呼吸困难", "意识模糊", "严重出血", "剧烈头痛",
        "持续呕吐", "高热不退", "突然晕厥", "剧烈腹痛", "面部下垂"
    ]

    # 中风险症状关键词
    medium_risk_keywords = ["持续", "加重", "反复", "严重", "剧烈"]

    risk_level = "low"
    reasons = []

    # 检查高风险症状
    for symptom in symptom_list:
        for high_risk in high_risk_symptoms:
            if high_risk in symptom:
                risk_level = "high"
                reasons.append(f"检测到高风险症状：{symptom}")
                break

    # 检查中等风险特征
    if risk_level == "low":
        for symptom in symptom_list:
            if any(keyword in symptom for keyword in medium_risk_keywords):
                risk_level = "medium"
                reasons.append(f"症状描述提示需要关注：{symptom}")

    # 生成建议
    if risk_level == "high":
        recommendation = "⚠️ 建议立即就医或拨打急救电话120"
    elif risk_level == "medium":
        recommendation = "建议尽快就医，不要拖延，必要时前往医院"
    else:
        recommendation = "建议密切观察症状变化，如果症状加重或持续不缓解，应及时就医"

    # 从 RAG 知识库获取风险相关的医学建议
    kb_advice = None
    try:
        kb = get_knowledge_base()
        # 根据风险等级查询相关医学知识
        risk_query = f"{symptoms} 紧急程度 风险评估 就医建议"
        results = kb.search(
            query=risk_query,
            top_k=1,
            filter_type=None
        )
        if results and results[0]["score"] > 0.5:
            kb_advice = results[0]["content"][:300]  # 限制长度
    except Exception as e:
        logger.warning(f"Failed to get KB advice: {e}")

    return {
        "answer": format_assessment(symptoms, risk_level, reasons, recommendation, kb_advice),
        "risk_level": risk_level,
        "recommendation": recommendation,
        "kb_advice": kb_advice
    }


def format_assessment(symptoms: str, level: str, reasons: list, recommendation: str, kb_advice: str = None) -> str:
    """格式化风险评估结果"""
    level_map = {
        "low": "低危 🟢",
        "medium": "中危 🟡",
        "high": "高危 🔴",
        "emergency": "紧急 🚨"
    }

    output = [
        f"【症状风险评估】",
        f"\n症状描述：{symptoms}",
        f"\n风险等级：{level_map.get(level, level)}",
    ]

    if reasons:
        output.append("\n风险因素：")
        for reason in reasons:
            output.append(f"  • {reason}")

    output.append(f"\n就医建议：{recommendation}")

    # 添加来自知识库的医学建议
    if kb_advice:
        output.append("\n【医学知识库补充】")
        output.append(kb_advice)

    if level == "high" or level == "emergency":
        output.append("\n⚠️ 请立即就医或拨打 120！")

    output.append("\n💡 数据来源：风险规则引擎 + 医学知识库（Milvus RAG）")

    return "\n".join(output)


def assess_risk_sync(symptoms: str) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(assess_risk(symptoms))
