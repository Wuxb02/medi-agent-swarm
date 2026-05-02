"""
Disease Code Skill
疾病编码查询 Skill（自包含，无需依赖tools）
"""
from typing import Dict, Any
from loguru import logger

# 全局知识库实例
_kb_instance = None

def get_knowledge_base():
    global _kb_instance
    if _kb_instance is None:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        _kb_instance = MedicalKnowledgeBase()
    return _kb_instance


async def disease_code(disease_name: str) -> Dict[str, Any]:
    """
    查询疾病 ICD-10 编码

    Args:
        disease_name: 疾病名称

    Returns:
        {
            "answer": "格式化的疾病编码信息",
            "icd10_code": "ICD-10 编码",
            "category": "疾病分类"
        }
    """
    logger.info(f"Searching ICD-10 code for disease: {disease_name}")

    # 使用知识库单例
    kb = get_knowledge_base()

    # 使用 Milvus 检索疾病编码
    results = kb.search(
        query=f"{disease_name} ICD-10编码 疾病分类",
        top_k=1,
        filter_type="disease_classification"
    )

    if results and results[0]["score"] > 0.1:
        doc = results[0]
        metadata = doc["metadata"]

        # 从内容中提取 ICD-10 编码（简单解析）
        content = doc["content"]
        icd10_code = metadata.get("icd10_code", "")
        if not icd10_code and "ICD-10编码：" in content:
            # 从内容中提取
            lines = content.split("\n")
            for line in lines:
                if "ICD-10编码：" in line:
                    icd10_code = line.split("：")[1].strip()
                    break

        return {
            "answer": format_code_info(disease_name, content),
            "icd10_code": icd10_code,
            "category": metadata.get("category", ""),
            "source": "向量数据库"
        }
    else:
        # 未找到相关内容
        logger.warning(f"No ICD-10 code found in vector DB for {disease_name}")
        return {
            "answer": f"未找到'{disease_name}'的ICD-10编码，建议使用更标准的疾病名称或联系医生咨询。",
            "icd10_code": "",
            "category": "",
            "source": "未找到"
        }


def format_code_info(disease_name: str, content: str) -> str:
    """格式化疾病编码信息"""
    output = [
        f"【疾病编码信息】\n",
        content
    ]

    return "\n".join(output)


def disease_code_sync(disease_name: str) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(disease_code(disease_name))
