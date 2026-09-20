"""
MedicalKnowledgeBase 实体索引 key 绑定测试

验证实体倒排索引的文档粒度是**逻辑 document_id**，与 search() 中
``entity_boost.get(document_id)`` 的取值口径一致，而非 Milvus 物理 doc_id。
"""

from types import SimpleNamespace

from mediZJ.knowledge.entity_index import MedicalEntityIndex
from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase


def _kb_with_rows(rows):
    """绕过 __init__（避免加载 embedding 模型与连接 Milvus）构造被测实例"""
    kb = object.__new__(MedicalKnowledgeBase)
    kb.collection_name = "test_collection"
    kb.entity_index = MedicalEntityIndex()
    kb.milvus_client = SimpleNamespace(query=lambda **kwargs: rows)
    return kb


class TestBuildEntityIndexKey:
    """_build_entity_index 应按逻辑 document_id 聚合"""

    def test_aggregates_by_logical_document_id(self):
        rows = [
            {"doc_id": "kv_abc", "document_id": "guideline_高血压", "text": "高血压 起始剂量"},
            {"doc_id": "kv_abc", "document_id": "guideline_高血压", "text": "ACEI 类药物禁忌"},
        ]
        kb = _kb_with_rows(rows)
        kb._build_entity_index()

        # 索引 key 是逻辑 id，不是物理 version_id
        assert "guideline_高血压" in kb.entity_index.search("高血压")
        assert "kv_abc" not in kb.entity_index.entity_to_docs.get("高血压", set())
        # 同一文档的多个 chunk 全部参与聚合
        assert "guideline_高血压" in kb.entity_index.search("ACEI")
        assert kb.entity_index._doc_count == 1

    def test_falls_back_to_physical_id_for_legacy_rows(self):
        """历史数据没有 document_id 字段时退化为物理 doc_id，仍与检索侧口径一致"""
        rows = [{"doc_id": "lifestyle_饮食", "text": "高血压患者低盐饮食"}]
        kb = _kb_with_rows(rows)
        kb._build_entity_index()

        assert "lifestyle_饮食" in kb.entity_index.search("高血压")

    def test_empty_collection_keeps_index_empty(self):
        kb = _kb_with_rows([])
        kb._build_entity_index()
        assert kb.entity_index.search("高血压") == {}
        assert kb.entity_index._doc_count == 0


class TestIndexDocument:
    """_index_document 按逻辑文档整篇重建"""

    def test_replaces_entries_with_remaining_chunks(self):
        kb = _kb_with_rows([{"text": "糖尿病 二甲双胍"}])
        kb.entity_index.add_document("guideline_高血压", "高血压 旧版内容")

        kb._index_document("guideline_高血压")

        assert "guideline_高血压" in kb.entity_index.search("糖尿病")
        assert kb.entity_index.search("高血压") == {}
        assert kb.entity_index._doc_count == 1

    def test_removes_entries_when_no_chunk_left(self):
        kb = _kb_with_rows([])
        kb.entity_index.add_document("guideline_高血压", "高血压")

        kb._index_document("guideline_高血压")

        assert kb.entity_index.search("高血压") == {}
        assert kb.entity_index._doc_count == 0

    def test_ignores_empty_document_id(self):
        kb = _kb_with_rows([{"text": "高血压"}])
        kb.entity_index.add_document("guideline_高血压", "高血压")

        kb._index_document("")

        assert kb.entity_index._doc_count == 1
