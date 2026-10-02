"""将仓库内置文档登记为所有用户共享的默认知识。"""

import hashlib
import re
from pathlib import Path

from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.jobs import enqueue
from mediZJ.knowledge.catalog import KnowledgeCatalog

DOCUMENTS_DIR = Path(__file__).parent / "data" / "documents"
DOCUMENT_TYPES = {
    "lifestyle": ("lifestyle", "生活方式建议数据库"),
    "symptoms": ("symptoms", "症状处理数据库"),
    "icd10": ("disease_classification", "ICD-10疾病编码数据库"),
    "guideline": ("clinical_guideline", "临床指南数据库"),
}


async def seed_default_documents(directory: Path = DOCUMENTS_DIR) -> int:
    """原子登记缺失文档和索引任务，保留已有文档及管理员修改。"""
    files = sorted(directory.glob("*.txt"))
    if not files:
        raise RuntimeError(f"默认知识文档目录为空：{directory}")

    catalog = KnowledgeCatalog()
    added = 0
    async with transaction() as conn:
        # 与上传和索引激活共用锁，避免并发初始化重复登记。
        await conn.execute(
            "SELECT name FROM admission WHERE name='knowledge' FOR UPDATE"
        )
        for path in files:
            _, kind, disease = path.stem.split("_", 2)
            doc_type, source = DOCUMENT_TYPES[kind]
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                raise ValueError(f"默认知识文档为空：{path.name}")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            safe_name = re.sub(r"[^\w]", "_", path.stem)
            document_id = f"{doc_type}_{safe_name}"
            existing = (
                await conn.execute(
                    "SELECT version_id FROM knowledge_documents "
                    "WHERE (document_id=%s OR content_hash=%s) "
                    "AND status!='failed' LIMIT 1",
                    (document_id, content_hash),
                )
            ).fetchone()
            if existing:
                continue
            metadata = {
                "filename": path.name,
                "type": doc_type,
                "disease": disease,
                "source": source,
                "content_hash": content_hash,
                # 内置文档不因自动导入而获得额外的可信来源认证。
                "authority_level": "user",
            }
            version = await catalog.begin_version(
                document_id, content_hash, metadata
            )
            await conn.execute(
                "UPDATE knowledge_documents SET content=%s WHERE version_id=%s",
                (content, version["version_id"]),
            )
            await enqueue(
                "knowledge_index",
                f"knowledge:{version['version_id']}",
                {"version_id": version["version_id"], "metadata": metadata},
            )
            added += 1
    return added
