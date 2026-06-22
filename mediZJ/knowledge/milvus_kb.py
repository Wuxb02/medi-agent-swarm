"""
医学知识库（Milvus）— 三路混合检索

检索路径：
  Path 1 — 稠密向量 : bge-small-zh-v1.5 → Milvus IP ANN
  Path 2 — BM25 稀疏 : jieba 客户端编码 → SPARSE_FLOAT_VECTOR
  Path 3 — 医学实体精确匹配 : jieba + 内存倒排索引

融合策略：Milvus RRF (Path1+2) + App-level Entity Boost (Path3)

兼容性：search() 签名与返回值结构保持不变，所有 Skill 无需改动
"""
import json
import math
import pickle
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger

from pymilvus import (
    MilvusClient, DataType,
    AnnSearchRequest, RRFRanker,
)
from pymilvus.model.sparse import BM25EmbeddingFunction
from pymilvus.model.sparse.bm25.tokenizers import build_default_analyzer
from sentence_transformers import SentenceTransformer

from mediZJ.knowledge.entity_index import MedicalEntityIndex

COLLECTION_NAME = "medical_knowledge_v2"
BM25_PICKLE_PATH = (
    Path(__file__).resolve().parent / "data" / "bm25_model.pkl"
)


# ---- Trace 集成（可选）----
try:
    from trace import traced_span, SpanType, ToolAttributes as TraceToolAttrs
    _TRACE_AVAILABLE = True
except ImportError:
    _TRACE_AVAILABLE = False
    traced_span = None  # type: ignore
    SpanType = None  # type: ignore
    TraceToolAttrs = None  # type: ignore


@contextmanager
def _noop_ctx():
    yield None


class MedicalKnowledgeBase:
    """医学知识库（单例）"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        db_path: str = None,
        collection_name: str = COLLECTION_NAME,
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
    ):
        if db_path is None:
            import os
            db_path = os.path.join(os.path.dirname(__file__), "data", "milvus_lite.db")
        if hasattr(self, "_initialized"):
            return

        self.db_path = db_path
        self.collection_name = collection_name

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # ---- Embedding 模型 ----
        local_model_path = (
            Path.home() / ".cache" / "huggingface" / "hub"
            / "models--BAAI--bge-small-zh-v1.5" / "snapshots"
        )
        if local_model_path.exists():
            snapshots = sorted(
                local_model_path.iterdir(),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            model_path = str(snapshots[0]) if snapshots else embedding_model
            logger.info(f"Loading embedding model from local cache: {model_path}")
        else:
            model_path = embedding_model
            logger.info(f"Loading embedding model: {embedding_model}")

        self.embedding_model = SentenceTransformer(model_path, device="cpu")
        self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded (dimension={self.embedding_dim})")

        # ---- Milvus Client ----
        logger.info(f"Connecting to Milvus Lite: {db_path}")
        self.milvus_client = MilvusClient(db_path)

        # ---- 创建/校验 Collection Schema ----
        self._ensure_collection_schema(collection_name)

        # ---- Entity Index ----
        self.entity_index = MedicalEntityIndex()
        self._build_entity_index()

        # ---- BM25 稀疏编码器 ----
        self._init_bm25()

        self._initialized = True

    # ------------------------------------------------------------------
    # Schema 创建
    # ------------------------------------------------------------------

    def _ensure_collection_schema(self, collection_name: str):
        """如果 collection 已存在但 schema 不兼容，自动重建"""
        if not self.milvus_client.has_collection(collection_name):
            logger.info(f"Creating collection: {collection_name}")
            self._create_collection()
            return

        try:
            idx_info = self.milvus_client.describe_index(
                collection_name, "sparse_vector",
            )
            metric_type = idx_info.get("metric_type", "")
        except Exception:
            metric_type = ""

        if metric_type != "IP":
            logger.warning(
                f"检测到旧 schema（sparse_vector metric_type={metric_type}），"
                f"自动重建 collection..."
            )
            self._delete_and_recreate()

    def _delete_and_recreate(self):
        """删除旧 collection 并用新 schema 重建"""
        self.milvus_client.drop_collection(self.collection_name)
        logger.info(f"已删除旧 collection: {self.collection_name}")
        self._create_collection()
        data_path = Path(self.db_path)
        bm25_path = (
            data_path.parent / "bm25_model.pkl"
            if data_path.name == "milvus_lite.db"
            else Path("bm25_model.pkl")
        )
        if bm25_path.exists():
            bm25_path.unlink()
            logger.debug("已删除旧 BM25 pickle（与旧 schema 不兼容）")

    def _create_collection(self):
        """创建显式 Schema 的 collection（稀疏向量由客户端 BM25EmbeddingFunction 编码）"""
        schema = MilvusClient.create_schema(
            auto_id=True, enable_dynamic_field=True,
        )

        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=256)
        schema.add_field("doc_type", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_id", DataType.INT64)
        schema.add_field("total_chunks", DataType.INT64)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)

        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=self.embedding_dim)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

        index_params = self.milvus_client.prepare_index_params()
        index_params.add_index("dense_vector", index_type="FLAT", metric_type="IP")
        index_params.add_index(
            "sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP",
        )

        self.milvus_client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )
        logger.info("Collection created (sparse vector via client-side BM25)")

    def _build_entity_index(self):
        """从当前 collection 的文档文本构建实体倒排索引"""
        try:
            rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter="id >= 0",
                output_fields=["doc_id", "text"],
                limit=16384,
            )
        except Exception as e:
            logger.warning(f"Failed to query docs for entity index: {e}")
            return

        if not rows:
            return

        # 按 doc_id 去重（每个 doc 只取第一条 chunk 文本即可）
        seen: set = set()
        docs: List[Dict] = []
        for row in rows:
            doc_id = row.get("doc_id", "")
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                docs.append({"doc_id": doc_id, "text": row.get("text", "")})

        self.entity_index.build_from_kb(docs)

    # ------------------------------------------------------------------
    # BM25 稀疏编码器
    # ------------------------------------------------------------------

    @staticmethod
    def _sparse_row_to_dict(row: Any) -> Dict[int, float]:
        """将单个稀疏矩阵行转换为 Milvus 所需的 Dict[int, float] 格式"""
        coo = row.tocoo()
        return {int(c): float(v) for c, v in zip(coo.col, coo.data)}

    @classmethod
    def _sparse_rows_to_dict_list(cls, matrix: Any) -> List[Dict[int, float]]:
        """将稀疏矩阵转换为 List[Dict[int, float]] 格式"""
        result = []
        for i in range(matrix.shape[0]):
            result.append(cls._sparse_row_to_dict(matrix[i]))
        return result

    def _init_bm25(self) -> None:
        """初始化 BM25 稀疏编码器：优先加载 pickle，否则从 Collection 已有文档拟合"""
        analyzer = build_default_analyzer(language="zh")
        self.bm25_ef = BM25EmbeddingFunction(analyzer)
        self._bm25_fitted = False

        if BM25_PICKLE_PATH.exists():
            try:
                with open(BM25_PICKLE_PATH, "rb") as f:
                    self.bm25_ef = pickle.load(f)
                self._bm25_fitted = True
                logger.info(
                    f"BM25 model loaded from pickle "
                    f"(dim={self.bm25_ef.dim})"
                )
                return
            except Exception as e:
                logger.warning(f"Failed to load BM25 pickle, will rebuild: {e}")

        # 从已有文档构建
        try:
            rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter="id >= 0",
                output_fields=["text"],
                limit=16384,
            )
        except Exception as e:
            logger.warning(f"Failed to query docs for BM25 fit: {e}")
            return

        if rows:
            texts = [r.get("text", "") for r in rows if r.get("text", "")]
            if texts:
                self.bm25_ef.fit(texts)
                self._bm25_fitted = True
                self._save_bm25()
                logger.info(
                    f"BM25 model fitted on {len(texts)} chunks "
                    f"(dim={self.bm25_ef.dim})"
                )

    def _bm25_encode_documents(self, texts: List[str]) -> List[Dict[int, float]]:
        return self._sparse_rows_to_dict_list(
            self.bm25_ef.encode_documents(texts)
        )

    def _bm25_encode_query(self, query: str) -> Dict[int, float]:
        return self._sparse_row_to_dict(
            self.bm25_ef.encode_queries([query])[0]
        )

    def _save_bm25(self) -> None:
        """持久化 BM25 模型到 pickle"""
        BM25_PICKLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BM25_PICKLE_PATH, "wb") as f:
            pickle.dump(self.bm25_ef, f)
        logger.debug("BM25 model saved to pickle")

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 1024, overlap: int = 100) -> List[str]:
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start = end - overlap
        return chunks

    # ------------------------------------------------------------------
    # 文档 CRUD
    # ------------------------------------------------------------------

    def add_documents(
        self, documents: List[Dict[str, Any]], chunk_size: int = 1024,
    ) -> int:
        """
        添加文档到知识库（分块 + 向量化 + 插入）。

        Args:
            documents: 文档列表，每个文档含 ``id``, ``content``, ``metadata``
            chunk_size: 分块大小（字符数）

        Returns:
            成功添加的块数量
        """
        if not documents:
            logger.warning("No documents to add")
            return 0

        logger.info(
            f"Adding {len(documents)} documents to knowledge base "
            f"(chunk_size={chunk_size})..."
        )

        # 分块
        all_chunks = []
        for doc in documents:
            chunks = self._chunk_text(doc["content"], chunk_size=chunk_size)
            meta = doc.get("metadata", {})
            for i, chunk in enumerate(chunks):
                all_chunks.append({
                    "doc_id": doc["id"],
                    "doc_type": meta.get("type", ""),
                    "chunk_id": i,
                    "total_chunks": len(chunks),
                    "text": chunk,
                    # 余下的 metadata 字段保留为动态字段
                    "disease": meta.get("disease", ""),
                    "source": meta.get("source", ""),
                    "filename": meta.get("filename", ""),
                    "content_hash": meta.get("content_hash", ""),
                })

        logger.info(f"Split into {len(all_chunks)} chunks")

        # 向量化（dense + sparse）
        texts = [c["text"] for c in all_chunks]
        vectors = self.embedding_model.encode(texts, show_progress_bar=True)

        # BM25 稀疏向量：客户端 jieba 分词编码（覆盖 Milvus 内置英文分词器）
        if not self._bm25_fitted:
            self.bm25_ef.fit(texts)
            self._bm25_fitted = True
            self._save_bm25()
            logger.info(f"BM25 model fitted on {len(texts)} chunks")
        sparse_vectors = self._bm25_encode_documents(texts)

        # 组装插入数据
        data = []
        for i, chunk in enumerate(all_chunks):
            entry: Dict[str, Any] = {
                "doc_id": chunk["doc_id"],
                "doc_type": chunk["doc_type"],
                "chunk_id": chunk["chunk_id"],
                "total_chunks": chunk["total_chunks"],
                "text": chunk["text"],
                "dense_vector": vectors[i].tolist(),
                "sparse_vector": sparse_vectors[i],
                # 动态字段
                "disease": chunk["disease"],
                "source": chunk["source"],
                "filename": chunk["filename"],
                "content_hash": chunk["content_hash"],
            }
            data.append(entry)

        self.milvus_client.insert(self.collection_name, data)
        logger.info(f"Successfully added {len(data)} chunks")

        # 更新 BM25 IDF 统计并持久化
        all_texts = [r.get("text", "") for r in self.milvus_client.query(
            collection_name=self.collection_name,
            filter="id >= 0",
            output_fields=["text"],
            limit=16384,
        )]
        if all_texts:
            self.bm25_ef.fit(all_texts)
            self._save_bm25()

        # 增量更新实体索引
        seen_doc_ids: set = set()
        for chunk in all_chunks:
            doc_id = chunk["doc_id"]
            if doc_id not in seen_doc_ids:
                seen_doc_ids.add(doc_id)
                self.entity_index.add_document(doc_id, chunk["text"])

        return len(data)

    # ------------------------------------------------------------------
    # 三路混合检索
    # ------------------------------------------------------------------

    def _hybrid_search(
        self,
        query: str,
        top_k: int,
        filter_expr: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Path 1+2: Dense + BM25 混合检索（Milvus RRF）"""
        query_vector = self.embedding_model.encode(
            [query], normalize_embeddings=True,
        )[0]

        dense_req = AnnSearchRequest(
            data=[query_vector.tolist()],
            anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": 16}},
            limit=top_k * 3,
            expr=filter_expr,
        )

        # BM25 稀疏向量：客户端 jieba 分词编码
        sparse_vec = self._bm25_encode_query(query)
        sparse_req = AnnSearchRequest(
            data=[sparse_vec],
            anns_field="sparse_vector",
            param={"metric_type": "IP"},
            limit=top_k * 3,
            expr=filter_expr,
        )

        results = self.milvus_client.hybrid_search(
            collection_name=self.collection_name,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=60),
            limit=top_k * 3,
            output_fields=[
                "id", "doc_id", "doc_type", "chunk_id",
                "total_chunks", "text",
            ],
        )
        # NaN/Inf 防护：扫描结果距离值并修复
        for hit in results[0]:
            dist = hit.get("distance", 0.0)
            if math.isnan(dist) or math.isinf(dist):
                hit["distance"] = 0.0
        return results[0]  # 单 query 取第一组

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        三路混合检索。

        Args:
            query: 查询文本
            top_k: 返回 Top-K 个去重文档
            filter_type: 可选文档类型过滤

        Returns:
            文档列表，每个文档含 ``id``, ``content``, ``metadata``, ``score``
        """
        logger.debug(
            f"Hybrid search: query={query[:80]} top_k={top_k} "
            f"filter_type={filter_type}"
        )

        _ctx = traced_span(SpanType.TOOL, name="knowledge_search") if _TRACE_AVAILABLE else _noop_ctx()
        with _ctx as t:
            if t and TraceToolAttrs:
                t.tool_attrs = TraceToolAttrs(
                    tool_name="knowledge_search",
                    arguments={"query": query[:200], "top_k": top_k, "filter_type": filter_type},
                )

            # Step 1: 实体加权（Path 3）
            entity_boost = self.entity_index.search(query)

            # Step 2: Dense + BM25 混合检索（Path 1+2）
            filter_expr = (
                f'doc_type == "{filter_type}"' if filter_type else None
            )
            try:
                hits = self._hybrid_search(query, top_k, filter_expr)
            except Exception as e:
                logger.error(f"Hybrid search failed: {e}")
                if t:
                    t.tool_attrs.result_summary = json.dumps({"error": str(e)}, ensure_ascii=False)
                return []

            # Step 3: RRF 归一化 + Entity Boost 加权
            ENTITY_BONUS_COEFFICIENT = 0.15
            RRF_K = 60
            MAX_RRF_SCORE = 2.0 / (RRF_K + 1)

            scoring_detail = []
            for hit in hits:
                doc_id = hit.get("entity", {}).get("doc_id", "")
                hit["_doc_id"] = doc_id
                hit["_text"] = hit.get("entity", {}).get("text", "")
                raw_rrf = hit.get("distance", 0.0)
                normalized_rrf = raw_rrf / MAX_RRF_SCORE if MAX_RRF_SCORE > 0 else raw_rrf
                bonus = entity_boost.get(doc_id, 0.0) * ENTITY_BONUS_COEFFICIENT
                hit["final_score"] = min(normalized_rrf + bonus, 1.0)
                scoring_detail.append({
                    "doc_id": doc_id,
                    "raw_rrf": round(raw_rrf, 6),
                    "normalized_rrf": round(normalized_rrf, 4),
                    "entity_bonus": round(bonus, 4),
                    "final_score": round(hit["final_score"], 4),
                })

            # Step 4: 按 final_score 重排
            hits.sort(key=lambda h: h["final_score"], reverse=True)

            # Step 5: 按 doc_id 去重，保留最高 final_score
            seen_docs: Dict[str, Dict[str, Any]] = {}
            for hit in hits:
                doc_id = hit["_doc_id"]
                if not doc_id:
                    doc_id = str(hit.get("id", ""))
                score = hit["final_score"]
                if doc_id not in seen_docs or score > seen_docs[doc_id]["score"]:
                    seen_docs[doc_id] = {
                        "id": hit.get("id"),
                        "content": hit["_text"],
                        "metadata": {
                            "doc_id": doc_id,
                            "type": hit.get("entity", {}).get("doc_type", ""),
                        },
                        "score": round(score, 4),
                    }

            # Step 6: 按分数排序，取 top_k
            top_docs = sorted(
                seen_docs.values(), key=lambda d: d["score"], reverse=True,
            )[:top_k]

            # Step 7: 还原完整文档内容（拼接所有 chunk），补充完整 metadata
            for doc in top_docs:
                doc_id = doc["metadata"].get("doc_id", "")
                if doc_id:
                    full_chunks = self.get_document_chunks(doc_id)
                    if full_chunks:
                        doc["content"] = "\n".join(
                            c["content"] for c in full_chunks
                        )
                        if full_chunks:
                            first_meta = full_chunks[0].get("metadata", {})
                            doc["metadata"] = first_meta

            # 回填 trace：记录完整计分过程
            if t:
                t.tool_attrs.result_summary = json.dumps({
                    "paths": ["dense_vector(IP)", "bm25_sparse", "entity_exact_match"],
                    "rrf_k": RRF_K,
                    "max_rrf_score": round(MAX_RRF_SCORE, 6),
                    "entity_bonus_coefficient": ENTITY_BONUS_COEFFICIENT,
                    "entity_boost_matches": len(entity_boost),
                    "raw_candidates": len(hits),
                    "scoring_top5": scoring_detail[:5],
                    "final_count": len(top_docs),
                    "top_score": top_docs[0]["score"] if top_docs else 0,
                }, ensure_ascii=False)
                t.tool_attrs.success = len(top_docs) > 0

            logger.debug(f"Found {len(top_docs)} unique documents")
            return top_docs

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------

    def delete_collection(self):
        """删除并重建 collection（用于重建）"""
        if self.milvus_client.has_collection(self.collection_name):
            self.milvus_client.drop_collection(self.collection_name)
            logger.info(f"Deleted collection: {self.collection_name}")
        self._create_collection()

    def count_documents(self) -> int:
        """统计去重后的文档数量"""
        try:
            return len(self.list_documents())
        except Exception as e:
            logger.warning(f"Failed to count documents: {e}")
            return 0

    def list_documents(self) -> List[Dict[str, Any]]:
        """列出知识库中所有去重后的文档摘要"""
        try:
            all_rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter="id >= 0",
                output_fields=["doc_id", "doc_type", "disease", "source",
                               "filename", "chunk_id"],
                limit=16384,
            )
        except Exception as e:
            logger.error(f"Failed to list documents: {e}")
            return []

        docs: Dict[str, Dict[str, Any]] = {}
        for row in all_rows:
            doc_id = row.get("doc_id", "unknown")
            if doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "filename": row.get("filename", ""),
                    "type": row.get("doc_type", ""),
                    "disease": row.get("disease", ""),
                    "source": row.get("source", ""),
                    "chunk_ids": set(),
                }
            chunk_id = row.get("chunk_id")
            if chunk_id is not None:
                docs[doc_id]["chunk_ids"].add(chunk_id)

        result = []
        for doc in docs.values():
            doc["chunk_count"] = len(doc["chunk_ids"])
            del doc["chunk_ids"]
            result.append(doc)

        return result

    def document_exists_by_hash(self, content_hash: str) -> bool:
        """根据内容 hash 检查文档是否已存在"""
        filter_expr = f'content_hash == "{content_hash}"'
        try:
            rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=["doc_id"],
                limit=1,
            )
            return len(rows) > 0
        except Exception:
            return False

    def get_document_chunks(self, doc_id: str) -> List[Dict[str, Any]]:
        """获取指定文档的所有 chunk，按 chunk_id 排序"""
        filter_expr = f'doc_id == "{doc_id}"'
        try:
            rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=[
                    "id", "chunk_id", "total_chunks", "text",
                    "doc_type", "disease", "source", "filename",
                    "content_hash",
                ],
                limit=16384,
            )
        except Exception as e:
            logger.error(f"Failed to get chunks for {doc_id}: {e}")
            return []

        chunks = []
        seen_chunk_ids: set = set()
        for row in rows:
            chunk_id = row.get("chunk_id", 0)
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            chunks.append({
                "milvus_id": row["id"],
                "chunk_id": chunk_id,
                "content": row.get("text", ""),
                "total_chunks": row.get("total_chunks", 0),
                "metadata": {
                    "doc_id": doc_id,
                    "type": row.get("doc_type", ""),
                    "disease": row.get("disease", ""),
                    "source": row.get("source", ""),
                    "filename": row.get("filename", ""),
                    "content_hash": row.get("content_hash", ""),
                    "chunk_id": chunk_id,
                    "total_chunks": row.get("total_chunks", 0),
                },
            })

        chunks.sort(key=lambda c: c["chunk_id"])
        return chunks

    def delete_document(self, doc_id: str) -> int:
        """删除指定文档的所有 chunk，返回删除数量"""
        chunks = self.get_document_chunks(doc_id)
        if not chunks:
            return 0

        filter_expr = f'doc_id == "{doc_id}"'
        try:
            self.milvus_client.delete(
                collection_name=self.collection_name,
                filter=filter_expr,
            )
            logger.info(f"Deleted {len(chunks)} chunks for doc_id={doc_id}")
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")
            return 0

        # 同步更新实体索引
        self.entity_index.remove_document(doc_id)
        return len(chunks)

    def update_document(
        self,
        doc_id: str,
        content: str,
        metadata: Dict[str, Any],
        chunk_size: int = 1024,
    ) -> int:
        """更新文档：删除旧 chunk，重新分块插入"""
        self.delete_document(doc_id)
        doc = {"id": doc_id, "content": content, "metadata": metadata}
        return self.add_documents([doc], chunk_size=chunk_size)
