"""
导入医学知识文档到 Milvus 知识库

数据来源：knowledge/data/documents/*.txt
文档分类：
- 01-09: 生活方式建议
- 10-19: ICD-10疾病编码
- 20-29: 临床指南
"""
import sys
from pathlib import Path
import re

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from knowledge.milvus_kb import MedicalKnowledgeBase


def load_documents_from_directory(doc_dir: Path) -> list:
    """从 documents 目录加载所有 txt 文件"""
    documents = []
    txt_files = sorted(doc_dir.glob("*.txt"))

    if not txt_files:
        logger.warning(f"No txt files found in {doc_dir}")
        return documents

    logger.info(f"Found {len(txt_files)} txt files in {doc_dir}")

    for txt_file in txt_files:
        try:
            content = txt_file.read_text(encoding='utf-8')

            # 从文件名推断文档类型
            filename = txt_file.stem  # 例如：01_lifestyle_hypertension
            parts = filename.split('_', 2)

            if len(parts) < 2:
                logger.warning(f"Skipping {txt_file.name}: invalid filename format")
                continue

            file_num = parts[0]
            doc_type_prefix = parts[1] if len(parts) > 1 else ""
            disease_name = parts[2] if len(parts) > 2 else ""

            # 确定文档类型和元数据
            if file_num.startswith('0') and int(file_num) < 10:
                # 01-09: 生活方式建议
                doc_type = "lifestyle"
                source = "生活方式建议数据库"
            elif 10 <= int(file_num) < 20:
                # 10-19: ICD-10疾病编码
                doc_type = "disease_classification"
                source = "ICD-10疾病编码数据库"
            elif 20 <= int(file_num) < 30:
                # 20-29: 临床指南
                doc_type = "clinical_guideline"
                source = "临床指南数据库"
            else:
                doc_type = "general"
                source = "医学知识库"

            # 从内容中提取疾病名称（如果有）
            if not disease_name:
                # 尝试从内容第一行提取
                first_line = content.split('\n')[0].strip()
                disease_name = first_line

            # 构建文档
            doc = {
                "id": f"{doc_type}_{filename}",
                "content": content,
                "metadata": {
                    "type": doc_type,
                    "disease": disease_name,
                    "source": source,
                    "filename": txt_file.name
                }
            }

            documents.append(doc)
            logger.debug(f"Loaded: {txt_file.name} -> type={doc_type}, disease={disease_name}")

        except Exception as e:
            logger.error(f"Error loading {txt_file.name}: {e}")
            continue

    return documents


def extract_documents_by_type(documents: list, doc_type: str) -> list:
    """按类型筛选文档"""
    return [doc for doc in documents if doc["metadata"]["type"] == doc_type]


def main():
    """主函数：加载文档并导入到 Milvus"""
    logger.info("=" * 70)
    logger.info("开始导入医学知识文档到 Milvus 知识库")
    logger.info("=" * 70)

    # 文档目录
    doc_dir = Path(__file__).parent.parent / "data" / "documents"

    if not doc_dir.exists():
        logger.error(f"Documents directory not found: {doc_dir}")
        logger.error("Please create the directory and add txt files")
        return

    # 加载所有文档
    logger.info(f"\n📚 从目录加载文档: {doc_dir}")
    all_docs = load_documents_from_directory(doc_dir)

    if not all_docs:
        logger.error("No documents loaded. Please add txt files to knowledge/data/documents/")
        return

    # 统计
    lifestyle_docs = extract_documents_by_type(all_docs, "lifestyle")
    icd10_docs = extract_documents_by_type(all_docs, "disease_classification")
    guideline_docs = extract_documents_by_type(all_docs, "clinical_guideline")
    general_docs = extract_documents_by_type(all_docs, "general")

    logger.info(f"\n✅ 总共加载 {len(all_docs)} 个文档")
    logger.info(f"   - 生活方式建议: {len(lifestyle_docs)}")
    logger.info(f"   - ICD-10编码: {len(icd10_docs)}")
    logger.info(f"   - 临床指南: {len(guideline_docs)}")
    logger.info(f"   - 其他: {len(general_docs)}")

    # 创建知识库实例
    kb = MedicalKnowledgeBase()

    # 导入数据
    logger.info("\n💾 导入到 Milvus...")
    num_added = kb.add_documents(all_docs)

    logger.info("\n" + "=" * 70)
    logger.info(f"🎉 完成！成功导入 {num_added} 个文档到知识库")
    logger.info("=" * 70)

    # 测试检索
    logger.info("\n🔍 测试语义检索...")
    test_queries = [
        ("血压高怎么办", "lifestyle"),
        ("糖尿病编码", "disease_classification"),
        ("高血压治疗指南", "clinical_guideline")
    ]

    for query, filter_type in test_queries:
        results = kb.search(query, top_k=1, filter_type=filter_type)
        if results:
            logger.info(f"  ✅ '{query}' → 找到 {len(results)} 个结果")
            logger.info(f"     最相关: {results[0]['metadata'].get('disease', 'N/A')} (相似度: {results[0]['score']:.3f})")
        else:
            logger.warning(f"  ⚠️  '{query}' → 未找到结果")


if __name__ == "__main__":
    main()
