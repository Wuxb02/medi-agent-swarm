"""
Analyze Symptoms Skill
症状分析 Skill（依赖 RAG 知识库）
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


async def analyze_symptoms(symptoms: str) -> Dict[str, Any]:
    """
    分析症状模式

    Args:
        symptoms: 症状描述（字符串）

    Returns:
        {
            "answer": "格式化的症状分析结果",
            "patterns": ["模式1", "模式2"],
            "possible_diseases": ["可能疾病1", "可能疾病2"]
        }
    """
    logger.info(f"Analyzing symptoms: {symptoms}")

    # 将症状字符串转换为列表
    symptom_list = [s.strip() for s in symptoms.split(",") if s.strip()]
    if not symptom_list:
        symptom_list = [symptoms]

    # 症状分类关键词
    symptom_categories = {
        "respiratory": {
            "keywords": ["咳嗽", "呼吸", "鼻塞", "喉咙", "气短", "痰", "胸闷"],
            "name": "呼吸系统"
        },
        "digestive": {
            "keywords": ["腹痛", "腹泻", "恶心", "呕吐", "胃痛", "便秘", "消化"],
            "name": "消化系统"
        },
        "neurological": {
            "keywords": ["头痛", "头晕", "眩晕", "失眠", "麻木", "乏力"],
            "name": "神经系统"
        },
        "cardiovascular": {
            "keywords": ["胸痛", "心悸", "气短", "心慌", "血压"],
            "name": "心血管系统"
        },
        "musculoskeletal": {
            "keywords": ["关节", "肌肉", "骨骼", "疼痛", "肿胀", "僵硬"],
            "name": "骨骼肌肉系统"
        }
    }

    # 检测症状所属类别
    detected_categories = []
    for category_id, category_data in symptom_categories.items():
        for symptom in symptom_list:
            if any(keyword in symptom for keyword in category_data["keywords"]):
                if category_id not in [c["id"] for c in detected_categories]:
                    detected_categories.append({
                        "id": category_id,
                        "name": category_data["name"]
                    })
                break

    # 生成分析总结
    patterns = []
    if detected_categories:
        category_names = [c["name"] for c in detected_categories]
        patterns.append(f"症状涉及：{', '.join(category_names)}")

    if len(detected_categories) > 1:
        patterns.append("涉及多个身体系统，建议全面检查")

    # 简单的疾病关联（基于症状类别）
    possible_diseases = []
    for cat in detected_categories:
        if cat["id"] == "respiratory":
            possible_diseases.extend(["感冒", "支气管炎", "肺炎"])
        elif cat["id"] == "digestive":
            possible_diseases.extend(["胃炎", "肠炎", "消化不良"])
        elif cat["id"] == "cardiovascular":
            possible_diseases.extend(["心绞痛", "高血压", "心律不齐"])
        elif cat["id"] == "neurological":
            possible_diseases.extend(["偏头痛", "神经衰弱", "脑供血不足"])

    # 去重
    possible_diseases = list(set(possible_diseases))[:5]  # 最多5个

    # 从 RAG 知识库获取更详细的疾病信息
    kb_insights = []
    if possible_diseases:
        try:
            kb = get_knowledge_base()
            # 查询最可能的前3个疾病的详细信息
            for disease in possible_diseases[:3]:
                results = kb.search(
                    query=f"{disease} 症状 诊断 鉴别",
                    top_k=1,
                    filter_type=None
                )
                if results and results[0]["score"] > 0.5:
                    kb_insights.append({
                        "disease": disease,
                        "info": results[0]["content"][:200]  # 限制长度
                    })
        except Exception as e:
            logger.warning(f"Failed to get KB insights: {e}")

    return {
        "answer": format_analysis(symptoms, patterns, possible_diseases, kb_insights),
        "patterns": patterns,
        "possible_diseases": possible_diseases,
        "kb_insights": kb_insights
    }


def format_analysis(symptoms: str, patterns: list, diseases: list, kb_insights: list = None) -> str:
    """格式化症状分析结果"""
    output = [
        f"【症状模式分析】",
        f"\n症状描述：{symptoms}",
    ]

    if patterns:
        output.append("\n识别到的症状模式：")
        for pattern in patterns:
            output.append(f"  • {pattern}")

    if diseases:
        output.append("\n可能关联的疾病：")
        for disease in diseases:
            output.append(f"  • {disease}")

    # 添加来自知识库的详细信息
    if kb_insights:
        output.append("\n【知识库补充信息】")
        for insight in kb_insights:
            output.append(f"\n关于 {insight['disease']}：")
            output.append(f"{insight['info']}")

    output.append("\n⚠️ 以上仅为模式分析，不能作为诊断依据。请咨询专业医生。")
    output.append("💡 数据来源：症状模式分析 + 医学知识库（Milvus RAG）")

    return "\n".join(output)


def analyze_symptoms_sync(symptoms: str) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(analyze_symptoms(symptoms))
