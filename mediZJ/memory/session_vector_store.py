"""
Milvus 会话向量存储

功能：
- 将会话摘要向量化并存入 Milvus Lite
- 语义搜索相似会话
- 支持会话向量的增删查

存储路径：memory/data/session_vectors.db
Collection：session_summaries
"""
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from pymilvus import MilvusClient

from .embedding import load_embedding_model


# 默认路径
_DEFAULT_VEC_DB_PATH = os.path.join(
    os.path.dirname(__file__), "data", "session_vectors.db"
)
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
        db_path: str = _DEFAULT_VEC_DB_PATH,
        embedding_model_name: str = _EMBEDDING_MODEL,
    ):
        if hasattr(self, "_initialized"):
            return

        self.db_path = db_path
        self.collection_name = _COLLECTION_NAME

        # 确保目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 加载 embedding 模型（进程内共享缓存实例）
        self._load_embedding_model(embedding_model_name)

        # Milvus Lite 客户端调用串行化（pymilvus 对本地文件型客户端无线程安全保证）
        self._client_lock = threading.RLock()

        # 初始化 Milvus Lite
        logger.info(f"Connecting to session vector store: {db_path}")
        self.milvus_client = MilvusClient(db_path)

        # 创建 collection（如不存在）
        if not self.milvus_client.has_collection(self.collection_name):
            logger.info(f"Creating collection: {self.collection_name}")
            self.milvus_client.create_collection(
                collection_name=self.collection_name,
                dimension=self.embedding_dim,
                metric_type="COSINE",
                auto_id=True,
            )

        self._initialized = True
        logger.info(
            f"SessionVectorStore initialized "
            f"(dim={self.embedding_dim}, collection={self.collection_name})"
        )

    def _load_embedding_model(self, model_name: str):
        """加载 embedding 模型（经共享缓存，全进程同一实例）"""
        self.embedding_model = load_embedding_model(model_name)
        self.embedding_dim = (
            self.embedding_model.get_sentence_embedding_dimension()
        )

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

        如果同 session_id 已存在，先删除旧记录再插入新记录。
        """
        if not summary_text.strip():
            logger.warning(f"Empty summary for session {session_id}, skip indexing")
            return

        # 向量化（CPU 推理，无需持锁）
        vector = self.embedding_model.encode([summary_text])[0]

        data = [
            {
                "vector": vector.tolist(),
                "session_id": session_id,
                "user_id": user_id,
                "summary": summary_text[:2000],  # 限制长度
                "mode": mode,
                "created_at": created_at,
                "total_tokens": total_tokens,
            }
        ]

        # delete + insert 在同一临界区内，保证更新原子性
        with self._client_lock:
            self.delete_session(session_id)
            try:
                self.milvus_client.insert(
                    collection_name=self.collection_name, data=data
                )
                logger.debug(f"Indexed session: {session_id}")
            except Exception as e:
                logger.error(f"Failed to index session {session_id}: {e}")

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
                    filter=f'user_id == "{user_id}"',
                    output_fields=[
                        "session_id", "user_id", "summary", "mode",
                        "created_at", "total_tokens",
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
                            "score": round(1 - hit["distance"], 4),
                        }
                    )

            logger.debug(f"Found {len(hits)} similar sessions for query")
            return hits

        except Exception as e:
            logger.error(f"Session similarity search failed: {e}")
            return []

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
            logger.warning(
                f"Failed to delete vector for session {session_id}: {e}"
            )

    def count_sessions(self) -> int:
        """统计已索引的会话数量"""
        try:
            with self._client_lock:
                stats = self.milvus_client.describe_collection(
                    self.collection_name
                )
            return stats.get("num_entities", 0)
        except Exception as e:
            logger.warning(f"Failed to count sessions: {e}")
            return 0
