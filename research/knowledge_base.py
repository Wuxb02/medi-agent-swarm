"""
本地知识库模块

基于 Qdrant 向量数据库，支持医学文档的存储和检索
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger
import uuid
import asyncio

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False
    logger.warning("Qdrant client not available, will use in-memory storage")

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    logger.warning("sentence-transformers not available, embeddings disabled")


@dataclass
class Document:
    """文档数据结构"""
    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    vector: Optional[List[float]] = None
    score: float = 0.0  # 相似度分数
    created_at: datetime = field(default_factory=datetime.now)


class KnowledgeBase:
    """
    本地知识库

    功能：
    - 文档向量化和存储
    - 基于向量相似度的检索
    - 支持 Qdrant 向量数据库或内存存储
    """

    def __init__(
        self,
        collection_name: str = "medical_knowledge",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        use_memory: bool = False  # 是否强制使用内存存储
    ):
        """
        初始化知识库

        Args:
            collection_name: 集合名称
            embedding_model: Embedding 模型名称
            qdrant_host: Qdrant 服务地址
            qdrant_port: Qdrant 服务端口
            use_memory: 是否使用内存存储
        """
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model
        self.use_memory = use_memory

        # 初始化 Embedding 模型（仅在需要时加载）
        self.embedding_model = None
        self.vector_size = 0

        # 只有在不使用内存存储时才加载 Embedding 模型
        if not use_memory and EMBEDDING_AVAILABLE:
            try:
                logger.info(f"Loading embedding model: {embedding_model}")
                self.embedding_model = SentenceTransformer(embedding_model)
                self.vector_size = self.embedding_model.get_sentence_embedding_dimension()
                logger.info(f"Embedding model loaded (dimension={self.vector_size})")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
        else:
            if use_memory:
                logger.info("Using in-memory storage, skipping embedding model loading")

        # 初始化存储后端
        self.qdrant_client = None
        self.memory_storage: Dict[str, Document] = {}  # 内存存储

        if not use_memory and QDRANT_AVAILABLE and self.embedding_model:
            try:
                # 尝试连接 Qdrant
                self.qdrant_client = QdrantClient(host=qdrant_host, port=qdrant_port)
                self._init_collection()
                logger.info(f"Connected to Qdrant at {qdrant_host}:{qdrant_port}")
            except Exception as e:
                logger.warning(f"Failed to connect to Qdrant: {e}, using in-memory storage")
                self.qdrant_client = None
        else:
            logger.info("Using in-memory storage for knowledge base")

    def _init_collection(self):
        """初始化 Qdrant 集合"""
        if not self.qdrant_client:
            return

        try:
            # 检查集合是否存在
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]

            if self.collection_name not in collection_names:
                # 创建集合
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE
                    )
                )
                logger.info(f"Created collection: {self.collection_name}")
            else:
                logger.info(f"Collection already exists: {self.collection_name}")

        except Exception as e:
            logger.error(f"Failed to initialize collection: {e}")
            self.qdrant_client = None

    def _embed_text(self, text: str) -> Optional[List[float]]:
        """
        文本向量化

        Args:
            text: 输入文本

        Returns:
            向量表示
        """
        if not self.embedding_model:
            return None

        try:
            vector = self.embedding_model.encode(text, convert_to_numpy=True)
            return vector.tolist()
        except Exception as e:
            logger.error(f"Failed to embed text: {e}")
            return None

    async def add_document(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None
    ) -> str:
        """
        添加文档到知识库

        Args:
            content: 文档内容
            metadata: 元数据
            doc_id: 文档ID（可选）

        Returns:
            文档ID
        """
        if doc_id is None:
            doc_id = str(uuid.uuid4())

        if metadata is None:
            metadata = {}

        # 向量化
        vector = self._embed_text(content)
        if vector is None:
            logger.warning("Embedding failed, document not added")
            return ""

        # 创建文档对象
        document = Document(
            id=doc_id,
            content=content,
            metadata=metadata,
            vector=vector
        )

        # 存储
        if self.qdrant_client:
            # 存储到 Qdrant
            try:
                point = PointStruct(
                    id=doc_id,
                    vector=vector,
                    payload={
                        "content": content,
                        "metadata": metadata,
                        "created_at": document.created_at.isoformat()
                    }
                )
                self.qdrant_client.upsert(
                    collection_name=self.collection_name,
                    points=[point]
                )
                logger.info(f"Document added to Qdrant: {doc_id}")
            except Exception as e:
                logger.error(f"Failed to add document to Qdrant: {e}")
                return ""
        else:
            # 存储到内存
            self.memory_storage[doc_id] = document
            logger.info(f"Document added to memory: {doc_id}")

        return doc_id

    async def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.0,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """
        检索相似文档

        Args:
            query: 查询文本
            top_k: 返回结果数量
            score_threshold: 相似度阈值
            filters: 过滤条件

        Returns:
            相似文档列表
        """
        # 向量化查询
        query_vector = self._embed_text(query)
        if query_vector is None:
            logger.warning("Query embedding failed")
            return []

        if self.qdrant_client:
            # 从 Qdrant 检索
            try:
                search_result = self.qdrant_client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=top_k,
                    score_threshold=score_threshold
                )

                documents = []
                for hit in search_result:
                    doc = Document(
                        id=hit.id,
                        content=hit.payload.get("content", ""),
                        metadata=hit.payload.get("metadata", {}),
                        score=hit.score
                    )
                    documents.append(doc)

                logger.info(f"Found {len(documents)} documents from Qdrant")
                return documents

            except Exception as e:
                logger.error(f"Qdrant search error: {e}")
                return []
        else:
            # 从内存检索（简单的余弦相似度）
            results = []
            for doc in self.memory_storage.values():
                if doc.vector:
                    # 计算余弦相似度
                    score = self._cosine_similarity(query_vector, doc.vector)
                    if score >= score_threshold:
                        doc.score = score
                        results.append(doc)

            # 按分数排序
            results.sort(key=lambda x: x.score, reverse=True)
            results = results[:top_k]

            logger.info(f"Found {len(results)} documents from memory")
            return results

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        import math

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(b * b for b in vec2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    async def bulk_add(
        self,
        documents: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> int:
        """
        批量添加文档

        Args:
            documents: 文档列表，每个文档包含 'content' 和 'metadata'
            batch_size: 批处理大小

        Returns:
            成功添加的文档数量
        """
        added_count = 0

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]

            for doc in batch:
                doc_id = await self.add_document(
                    content=doc.get("content", ""),
                    metadata=doc.get("metadata", {})
                )
                if doc_id:
                    added_count += 1

            logger.info(f"Batch {i // batch_size + 1}: Added {added_count}/{len(documents)} documents")

        return added_count

    def get_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        if self.qdrant_client:
            try:
                collection_info = self.qdrant_client.get_collection(self.collection_name)
                return {
                    "backend": "qdrant",
                    "collection": self.collection_name,
                    "count": collection_info.points_count,
                    "vector_size": self.vector_size
                }
            except Exception as e:
                logger.error(f"Failed to get Qdrant stats: {e}")
                return {"backend": "qdrant", "error": str(e)}
        else:
            return {
                "backend": "memory",
                "count": len(self.memory_storage),
                "vector_size": self.vector_size if self.embedding_model else 0
            }


# 便捷函数
async def search_knowledge_base(
    query: str,
    top_k: int = 5
) -> List[Document]:
    """
    快速检索知识库

    Args:
        query: 查询文本
        top_k: 返回结果数量

    Returns:
        相似文档列表
    """
    kb = KnowledgeBase()
    return await kb.search(query, top_k=top_k)
