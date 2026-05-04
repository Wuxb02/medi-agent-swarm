"""
清理 Milvus 知识库中的重复数据

按 doc_id + chunk_id 去重，每个组合只保留一条记录。
"""
import sys
from pathlib import Path
import json

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from knowledge.milvus_kb import MedicalKnowledgeBase


def main():
    kb = MedicalKnowledgeBase()

    # 查询所有数据
    all_rows = kb.milvus_client.query(
        collection_name=kb.collection_name,
        filter="id >= 0",
        output_fields=["metadata"],
        limit=16384
    )
    logger.info(f"清理前总记录数: {len(all_rows)}")

    # 按 doc_id + chunk_id 分组
    groups = {}
    for row in all_rows:
        try:
            meta = json.loads(row["metadata"])
            key = (meta.get("doc_id", ""), meta.get("chunk_id", -1))
            if key not in groups:
                groups[key] = []
            groups[key].append(row["id"])
        except (json.JSONDecodeError, KeyError):
            continue

    # 找出需要删除的重复 ID
    dup_ids = []
    for key, ids in groups.items():
        if len(ids) > 1:
            dup_ids.extend(ids[1:])  # 保留第一个，删除其余

    logger.info(f"待删除重复记录: {len(dup_ids)} 条")

    if not dup_ids:
        logger.info("没有重复数据，无需清理")
        return

    # 删除重复记录
    kb.milvus_client.delete(
        collection_name=kb.collection_name,
        filter=f"id in {dup_ids}"
    )
    logger.info("删除完成")

    # 验证
    remaining = kb.milvus_client.query(
        collection_name=kb.collection_name,
        filter="id >= 0",
        output_fields=["metadata"],
        limit=16384
    )
    logger.info(f"清理后总记录数: {len(remaining)}")

    # 展示各文档 chunk 数
    doc_chunks = {}
    for row in remaining:
        try:
            meta = json.loads(row["metadata"])
            doc_id = meta.get("doc_id", "")
            if doc_id not in doc_chunks:
                doc_chunks[doc_id] = set()
            doc_chunks[doc_id].add(meta.get("chunk_id", -1))
        except (json.JSONDecodeError, KeyError):
            continue

    for doc_id, chunk_ids in sorted(doc_chunks.items()):
        logger.info(f"  {doc_id}: {len(chunk_ids)} chunks")


if __name__ == "__main__":
    main()
