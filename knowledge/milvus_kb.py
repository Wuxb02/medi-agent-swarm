"""
医学知识库（Milvus）

功能：
1. 文档向量化和存储
2. 语义检索
3. 知识库管理

参考实现：Milvus + 向量检索
"""
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger

from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer


class MedicalKnowledgeBase:
    """医学知识库"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        """实现单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        db_path: str = "./knowledge/data/milvus_lite.db",
        collection_name: str = "medical_knowledge",
        embedding_model: str = "BAAI/bge-small-zh-v1.5"
    ):
        """
        初始化医学知识库

        Args:
            db_path: Milvus Lite 数据库文件路径
            collection_name: Collection 名称
            embedding_model: Embedding 模型名称或本地路径
        """
        # 防止重复初始化
        if hasattr(self, '_initialized'):
            return

        self.db_path = db_path
        self.collection_name = collection_name

        # 确保数据目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 初始化 Embedding 模型（支持本地路径）
        # 优先检查本地缓存路径
        local_model_path = Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-small-zh-v1.5" / "snapshots"

        if local_model_path.exists():
            # 找到最新的 snapshot
            snapshots = sorted(local_model_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if snapshots:
                model_path = str(snapshots[0])
                logger.info(f"Loading embedding model from local cache: {model_path}")
                self.embedding_model = SentenceTransformer(model_path, device='cpu')
            else:
                logger.info(f"Loading embedding model: {embedding_model}")
                self.embedding_model = SentenceTransformer(embedding_model, device='cpu')
        else:
            logger.info(f"Loading embedding model: {embedding_model}")
            self.embedding_model = SentenceTransformer(embedding_model, device='cpu')

        self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded (dimension={self.embedding_dim})")

        # 初始化 Milvus Lite
        logger.info(f"Connecting to Milvus Lite: {db_path}")
        self.milvus_client = MilvusClient(db_path)

        # 创建 collection（如果不存在）
        if not self.milvus_client.has_collection(collection_name):
            logger.info(f"Creating collection: {collection_name}")
            self.milvus_client.create_collection(
                collection_name=collection_name,
                dimension=self.embedding_dim,
                metric_type="COSINE",  # 余弦相似度
                auto_id=True  # 自动生成整数ID
            )
        else:
            logger.info(f"Collection already exists: {collection_name}")

        self._initialized = True

    def _chunk_text(self, text: str, chunk_size: int = 1024, overlap: int = 100) -> List[str]:
        """
        分块文本

        Args:
            text: 原始文本
            chunk_size: 块大小（字符数）
            overlap: 重叠字符数

        Returns:
            文本块列表
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap  # 重叠

        return chunks

    def add_documents(self, documents: List[Dict[str, Any]], chunk_size: int = 1024) -> int:
        """
        添加文档到知识库（支持分块）

        Args:
            documents: 文档列表，每个文档包含 id, content, metadata
            chunk_size: 分块大小（字符数），默认 1024

        Returns:
            成功添加的文档块数量
        """
        if not documents:
            logger.warning("No documents to add")
            return 0

        logger.info(f"Adding {len(documents)} documents to knowledge base (chunk_size={chunk_size})...")

        # 分块并向量化
        all_chunks = []
        for doc in documents:
            chunks = self._chunk_text(doc["content"], chunk_size=chunk_size)
            for i, chunk in enumerate(chunks):
                metadata = doc.get("metadata", {}).copy()
                metadata["doc_id"] = doc["id"]
                metadata["chunk_id"] = i
                metadata["total_chunks"] = len(chunks)

                all_chunks.append({
                    "content": chunk,
                    "metadata": metadata
                })

        logger.info(f"Split into {len(all_chunks)} chunks")

        # 向量化
        contents = [chunk["content"] for chunk in all_chunks]
        vectors = self.embedding_model.encode(contents, show_progress_bar=True)

        # 准备数据
        data = []
        for i, chunk in enumerate(all_chunks):
            data.append({
                "vector": vectors[i].tolist(),
                "content": chunk["content"],
                "metadata": json.dumps(chunk["metadata"], ensure_ascii=False)
            })

        # 插入
        self.milvus_client.insert(self.collection_name, data)
        logger.info(f"Successfully added {len(data)} chunks")

        return len(data)

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        检索相关文档

        Args:
            query: 查询文本
            top_k: 返回top K个结果
            filter_type: 可选的类型过滤（如 "lifestyle", "disease_classification"）

        Returns:
            文档列表，每个文档包含 id, content, metadata, score
        """
        logger.debug(f"Searching for: {query} (top_k={top_k}, filter_type={filter_type})")

        # 向量化查询
        query_vector = self.embedding_model.encode([query])[0]

        # 构建过滤条件
        filter_expr = None
        if filter_type:
            filter_expr = f'metadata like "%\\"type\\": \\"{filter_type}\\"%"'

        # 检索（多取一些以支持去重）
        try:
            results = self.milvus_client.search(
                collection_name=self.collection_name,
                data=[query_vector.tolist()],
                limit=top_k * 3,
                filter=filter_expr,
                output_fields=["content", "metadata"]
            )
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

        # 格式化结果并按 doc_id 去重（保留最高分）
        seen_docs: Dict[str, Dict[str, Any]] = {}
        for hits in results:
            for hit in hits:
                try:
                    meta = json.loads(hit["entity"]["metadata"])
                    doc_id = meta.get("doc_id", str(hit["id"]))
                    score = 1 - hit["distance"]
                    if doc_id not in seen_docs or score > seen_docs[doc_id]["score"]:
                        seen_docs[doc_id] = {
                            "id": hit["id"],
                            "content": hit["entity"]["content"],
                            "metadata": meta,
                            "score": score
                        }
                except Exception as e:
                    logger.warning(f"Failed to parse result: {e}")
                    continue

        # 按分数排序，取 top_k
        top_docs = sorted(seen_docs.values(), key=lambda d: d["score"], reverse=True)[:top_k]

        # 还原完整文档内容（拼接所有 chunk）
        for doc in top_docs:
            doc_id = doc["metadata"].get("doc_id")
            if doc_id:
                full_chunks = self.get_document_chunks(doc_id)
                if full_chunks:
                    doc["content"] = "\n".join(c["content"] for c in full_chunks)

        logger.debug(f"Found {len(top_docs)} unique documents")
        return top_docs

    def delete_collection(self):
        """删除 collection（用于测试）"""
        if self.milvus_client.has_collection(self.collection_name):
            self.milvus_client.drop_collection(self.collection_name)
            logger.info(f"Deleted collection: {self.collection_name}")

    def count_documents(self) -> int:
        """统计文档数量"""
        try:
            stats = self.milvus_client.describe_collection(self.collection_name)
            # Note: Milvus Lite may not return accurate count, this is a best-effort
            return stats.get("num_entities", 0)
        except Exception as e:
            logger.warning(f"Failed to count documents: {e}")
            return 0

    def list_documents(self) -> List[Dict[str, Any]]:
        """列出知识库中所有去重后的文档摘要"""
        try:
            all_rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter="id >= 0",
                output_fields=["metadata"],
                limit=16384
            )
        except Exception as e:
            logger.error(f"Failed to list documents: {e}")
            return []

        docs: Dict[str, Dict[str, Any]] = {}
        for row in all_rows:
            try:
                meta = json.loads(row["metadata"])
            except (json.JSONDecodeError, KeyError):
                continue
            doc_id = meta.get("doc_id", "unknown")
            if doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "filename": meta.get("filename", ""),
                    "type": meta.get("type", ""),
                    "disease": meta.get("disease", ""),
                    "source": meta.get("source", ""),
                    "chunk_ids": set(),
                }
            chunk_id = meta.get("chunk_id")
            if chunk_id is not None:
                docs[doc_id]["chunk_ids"].add(chunk_id)

        # 转换 chunk_ids set 为 chunk_count
        result = []
        for doc in docs.values():
            doc["chunk_count"] = len(doc["chunk_ids"])
            del doc["chunk_ids"]
            result.append(doc)

        return result

    def document_exists_by_hash(self, content_hash: str) -> bool:
        """根据内容 hash 检查文档是否已存在"""
        filter_expr = f'metadata like "%\\"content_hash\\": \\"{content_hash}\\"%"'
        try:
            rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=["metadata"],
                limit=1
            )
            return len(rows) > 0
        except Exception:
            return False

    def get_document_chunks(self, doc_id: str) -> List[Dict[str, Any]]:
        """获取指定文档的所有 chunk，按 chunk_id 排序"""
        filter_expr = f'metadata like "%\\"doc_id\\": \\"{doc_id}\\"%"'
        try:
            rows = self.milvus_client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=["content", "metadata"],
                limit=16384
            )
        except Exception as e:
            logger.error(f"Failed to get chunks for {doc_id}: {e}")
            return []

        chunks = []
        seen_chunk_ids = set()
        for row in rows:
            try:
                meta = json.loads(row["metadata"])
            except (json.JSONDecodeError, KeyError):
                continue
            chunk_id = meta.get("chunk_id", 0)
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            chunks.append({
                "milvus_id": row["id"],
                "chunk_id": chunk_id,
                "content": row["content"],
                "total_chunks": meta.get("total_chunks", 0),
            })

        chunks.sort(key=lambda c: c["chunk_id"])
        return chunks

    def delete_document(self, doc_id: str) -> int:
        """删除指定文档的所有 chunk，返回删除数量"""
        chunks = self.get_document_chunks(doc_id)
        if not chunks:
            return 0

        filter_expr = f'metadata like "%\\"doc_id\\": \\"{doc_id}\\"%"'
        try:
            self.milvus_client.delete(
                collection_name=self.collection_name,
                filter=filter_expr
            )
            logger.info(f"Deleted {len(chunks)} chunks for doc_id={doc_id}")
            return len(chunks)
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")
            return 0

    def update_document(
        self,
        doc_id: str,
        content: str,
        metadata: Dict[str, Any],
        chunk_size: int = 1024
    ) -> int:
        """更新文档：删除旧 chunk，重新分块插入"""
        self.delete_document(doc_id)
        doc = {"id": doc_id, "content": content, "metadata": metadata}
        return self.add_documents([doc], chunk_size=chunk_size)
