"""
轻量级医学实体倒排索引

基于 jieba 分词从知识库文档中自抽取医学实体，构建内存倒排索引，
支持查询时实体精确命中加权。
"""
import re
from collections import defaultdict
from typing import Dict, List, Set

import jieba
from loguru import logger


# 医学相关的英文缩写/编码正则
_MEDICAL_CODE_RE = re.compile(
    r"^[A-Z]\d{2}(\.\d+)?$"         # ICD 编码: I10, E11.2
    r"|^[A-Z]{2,8}$"                # 英文缩写: ACEI, CCB, BMI, CT, MRI
    r"|^[A-Z][a-z]{2,}(ine|ol|ide|one|ase|cin|pril|lol|pine|mide|stam)$"  # 药品名后缀
    r"|^\d+(\.\d+)?(mg|g|ml|mmol|mmHg|%|℃)$"  # 剂量/单位
)

# 医学无关停用词
_STOP_WORDS = frozenset({
    "的", "和", "或", "与", "及", "等", "是", "有", "在", "中",
    "为", "不", "了", "也", "就", "都", "而", "且", "但", "以",
    "可", "要", "会", "能", "对", "从", "到", "上", "下", "一",
    "个", "种", "些", "这", "那", "其", "之", "将", "已", "于",
    "患者", "可能", "需要", "可以", "应该", "建议", "注意",
    "进行", "使用", "发生", "出现", "包括", "表现", "情况",
    "常见", "主要", "一般", "通常", "治疗", "药物", "疾病",
    "症状", "检查", "诊断", "预防", "控制", "管理", "方法",
    "方面", "以下", "以上", "包括", "其他", "所有", "不同",
    "\n", "\r", "\t", " ", "",
})


class MedicalEntityIndex:
    """轻量级医学实体倒排索引，从知识库文档自抽取实体"""

    def __init__(self):
        # entity → {doc_id, ...}
        self.entity_to_docs: Dict[str, Set[str]] = defaultdict(set)
        # entity → 文档频率（出现在多少个文档中）
        self._entity_df: Dict[str, int] = {}
        self._doc_count = 0

    def build_from_kb(self, documents: List[Dict]):
        """
        从知识库文档中抽取医学实体构建倒排索引。

        Args:
            documents: 文档列表，每项含 ``doc_id`` 与 ``text`` 字段
        """
        if not documents:
            logger.warning("No documents to build entity index")
            return

        self.entity_to_docs.clear()
        for doc in documents:
            entities = self._extract_entities(doc.get("text", ""))
            doc_id = doc.get("doc_id", "")
            if not doc_id:
                continue
            for entity in entities:
                self.entity_to_docs[entity].add(doc_id)

        self._doc_count = len(documents)
        self._entity_df = {entity: len(doc_ids) for entity, doc_ids in self.entity_to_docs.items()}
        logger.info(
            f"Entity index built: {len(self.entity_to_docs)} unique entities "
            f"from {self._doc_count} documents"
        )

    def _extract_entities(self, text: str) -> List[str]:
        """jieba 分词 + 医学术语过滤"""
        words = jieba.cut(text)
        entities: List[str] = []
        for w in words:
            w = w.strip()
            if self._is_medical_term(w):
                entities.append(w)
        # 去重保序
        seen: Set[str] = set()
        return [e for e in entities if not (e in seen or seen.add(e))]

    @staticmethod
    def _is_medical_term(word: str) -> bool:
        """判断是否为医学相关术语"""
        if len(word) < 2:
            return False
        if word in _STOP_WORDS:
            return False
        if _MEDICAL_CODE_RE.match(word):
            return True
        # 中文医学名词：均为中文字符，长度 2-12
        if all("一" <= c <= "鿿" for c in word) and 2 <= len(word) <= 12:
            return True
        return False

    def search(self, query: str) -> Dict[str, float]:
        """
        查询与 query 中医学实体匹配的 doc_id 及其加权分（IDF 加权）。

        Returns:
            {doc_id: boost_score}，boost_score 已归一化到 [0, 1]
        """
        from math import log

        query_entities = self._extract_entities(query)
        if not query_entities:
            return {}

        N = max(self._doc_count, 1)
        doc_scores: Dict[str, float] = defaultdict(float)
        for entity in query_entities:
            doc_ids = self.entity_to_docs.get(entity, set())
            if not doc_ids:
                continue
            # IDF 平滑加权：常见实体权重低，罕见实体权重高
            df = self._entity_df.get(entity, len(doc_ids))
            idf = log((N + 1) / (df + 1)) + 1.0
            for doc_id in doc_ids:
                doc_scores[doc_id] += idf

        max_score = max(doc_scores.values()) if doc_scores else 1.0
        return {k: v / max_score for k, v in doc_scores.items()}

    def add_document(self, doc_id: str, text: str):
        """增量添加单个文档的实体索引"""
        entities = self._extract_entities(text)
        for entity in entities:
            self.entity_to_docs[entity].add(doc_id)
        # 更新文档频率
        self._entity_df = {e: len(docs) for e, docs in self.entity_to_docs.items()}
        self._doc_count += 1

    def remove_document(self, doc_id: str):
        """移除单个文档的实体索引"""
        for entity, doc_ids in self.entity_to_docs.items():
            doc_ids.discard(doc_id)
        # 清理空集合
        self.entity_to_docs = defaultdict(
            set, {k: v for k, v in self.entity_to_docs.items() if v}
        )
        # 同步清理文档频率（空实体已不存在于 entity_to_docs）
        self._entity_df = {e: len(docs) for e, docs in self.entity_to_docs.items()}
        self._doc_count = max(0, self._doc_count - 1)
