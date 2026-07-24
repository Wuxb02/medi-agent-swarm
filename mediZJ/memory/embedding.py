"""
共享 embedding 工具模块

提供统一的模型加载和余弦相似度计算，
供 entropy_manager、session_vector_store 等模块复用。
"""
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from loguru import logger
from sentence_transformers import SentenceTransformer


_DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"


def _get_local_cache_path(model_name: str) -> Path | None:
    """查找模型的本地 HuggingFace 缓存路径"""
    cache_dir_name = model_name.replace("/", "--")
    local_path = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / f"models--{cache_dir_name}"
        / "snapshots"
    )
    if local_path.exists():
        snapshots = sorted(
            local_path.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if snapshots:
            return snapshots[0]
    return None


@lru_cache(maxsize=4)
def load_embedding_model(model_name: str | None = None) -> SentenceTransformer:
    """
    加载 embedding 模型，优先使用本地 HuggingFace 缓存。

    模型名称优先级：参数 > 环境变量 EMBEDDING_MODEL_NAME > 默认值。
    加载失败时抛出 RuntimeError。

    进程内缓存：同名模型全局共享一个实例（推理只读，可安全并发），
    避免 ShortTermMemory/SessionVectorStore 等各自重复加载占用双倍内存。

    Args:
        model_name: 模型名称或路径（可选）

    Returns:
        SentenceTransformer 实例
    """
    model_name = model_name or os.getenv("EMBEDDING_MODEL_NAME") or _DEFAULT_MODEL

    local_path = _get_local_cache_path(model_name)
    model_path = str(local_path) if local_path else model_name

    if local_path:
        logger.info(f"Loading embedding model from local cache: {model_path}")

    try:
        model = SentenceTransformer(model_path, device="cpu")
        dim = model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded: {model_name} (dim={dim})")
        return model
    except Exception as e:
        raise RuntimeError(f"Failed to load embedding model '{model_name}': {e}") from e


def batch_cosine_similarity(vectors: np.ndarray) -> np.ndarray:
    """
    计算向量组的 pairwise 余弦相似度矩阵。

    Args:
        vectors: shape (n, d) 的向量矩阵

    Returns:
        shape (n, n) 的相似度矩阵，对角线为 1.0
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    normalized = vectors / norms
    return normalized @ normalized.T
