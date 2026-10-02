"""
Milvus 会话向量存储

功能：
- 将会话摘要向量化并存入 Milvus 服务端
- 语义搜索相似会话
- 支持会话向量的增删查

存储：Milvus 服务端
Collection：session_summaries
"""

import threading
from typing import Any, Dict, List

from loguru import logger

from pymilvus import MilvusClient, DataType

from .embedding import load_embedding_model


_COLLECTION_NAME = "session_summaries"
_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


class SessionVectorStore:
    """Milvus 会话向量存储（单例模式）"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        embedding_model_name: str = None,
        initialize: bool = False,
    ):
        if hasattr(self, "_initialized"):
            return

        from mediZJ.infrastructure.settings import get_settings

        settings = get_settings()
        self.collection_name = settings.session_collection

        # 加载 embedding 模型（进程内共享缓存实例）
        if (
            embedding_model_name is not None
            and embedding_model_name != settings.embedding_model_name
        ):
            raise ValueError("禁止更换固定 embedding 模型")
        self._load_embedding_model(embedding_model_name)

        # Milvus 客户端调用串行化
        self._client_lock = threading.RLock()

        # 初始化 Milvus 服务端客户端
        self.milvus_client = MilvusClient(
            uri=settings.milvus_uri, token=settings.milvus_token
        )

        # 创建 collection（如不存在）
        if not self.milvus_client.has_collection(self.collection_name):
            if not initialize:
                raise RuntimeError("会话 collection 不存在，请先执行 bootstrap")
            from mediZJ.infrastructure.vector_schema import SCHEMA_DESCRIPTION

            self.milvus_client.create_collection(
                collection_name=self.collection_name,
                dimension=self.embedding_dim,
                description=SCHEMA_DESCRIPTION,
                metric_type="COSINE",
                auto_id=False,
                id_type="string",
                max_length=191,
            )

        from mediZJ.infrastructure.vector_schema import validate_collection

        validate_collection(
            self.milvus_client.describe_collection(self.collection_name),
            {
                "id": DataType.VARCHAR,
                "vector": DataType.FLOAT_VECTOR,
            },
            self.embedding_dim,
        )
        self._initialized = True
        logger.info(
            f"SessionVectorStore initialized "
            f"(dim={self.embedding_dim}, collection={self.collection_name})"
        )

    def _load_embedding_model(self, model_name: str):
        """加载 embedding 模型（经共享缓存，全进程同一实例）"""
        self.embedding_model = load_embedding_model(model_name)
        self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()

    def index_session(
        self,
        session_id: str,
        summary_text: str,
        user_id: str = "default",
        mode: str = "single",
        created_at: str = "",
        total_tokens: int = 0,
    ):
        """
        将会话摘要向量化并存入 Milvus

        同 session_id 使用稳定主键幂等 upsert。
        """
        if not summary_text.strip():
            logger.warning(f"Empty summary for session {session_id}, skip indexing")
            return

        # 向量化（自动选择推理设备，无需持锁）
        vector = self.embedding_model.encode([summary_text])[0]

        data = [
            {
                "id": session_id,
                "vector": vector.tolist(),
                "session_id": session_id,
                "user_id": user_id,
                "summary": summary_text[:2000],  # 限制长度
                "mode": mode,
                "created_at": created_at,
                "total_tokens": total_tokens,
            }
        ]

        with self._client_lock:
            self.milvus_client.upsert(collection_name=self.collection_name, data=data)

    def search_similar(
        self,
        query: str,
        top_k: int = 3,
        user_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """
        语义搜索相似会话

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            [{session_id, summary, score, mode, created_at}, ...]
        """
        if not query.strip():
            return []

        try:
            query_vector = self.embedding_model.encode([query])[0]

            with self._client_lock:
                results = self.milvus_client.search(
                    collection_name=self.collection_name,
                    data=[query_vector.tolist()],
                    limit=top_k,
                    consistency_level="Strong",
                    filter=f'user_id == "{user_id}"',
                    output_fields=[
                        "session_id",
                        "user_id",
                        "summary",
                        "mode",
                        "created_at",
                        "total_tokens",
                    ],
                )

            hits = []
            for result_set in results:
                for hit in result_set:
                    entity = hit["entity"]
                    hits.append(
                        {
                            "session_id": entity["session_id"],
                            "summary": entity["summary"],
                            "mode": entity.get("mode", "single"),
                            "created_at": entity.get("created_at", ""),
                            "total_tokens": entity.get("total_tokens", 0),
                            "score": round(hit["distance"], 4),
                        }
                    )

            logger.debug(f"Found {len(hits)} similar sessions for query")
            return hits

        except Exception as e:
            logger.error("会话向量检索失败: {}", type(e).__name__)
            raise

    def delete_session(self, session_id: str):
        """删除会话的向量记录"""
        try:
            with self._client_lock:
                self.milvus_client.delete(
                    collection_name=self.collection_name,
                    filter=f'session_id == "{session_id}"',
                )
            logger.debug(f"Deleted vector for session: {session_id}")
        except Exception as e:
            logger.error("会话向量删除失败: {}", type(e).__name__)
            raise

    def count_sessions(self) -> int:
        """统计已索引的会话数量"""
        try:
            with self._client_lock:
                rows = self.milvus_client.query(
                    collection_name=self.collection_name,
                    filter="",
                    output_fields=["count(*)"],
                    consistency_level="Strong",
                )
            return int(rows[0]["count(*)"])
        except Exception as e:
            logger.warning(f"Failed to count sessions: {type(e).__name__}")
            return 0
