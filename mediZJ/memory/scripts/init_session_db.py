"""
初始化会话持久化数据库

创建 SQLite 会话数据库和 Milvus 向量索引所需的表和集合。
首次运行项目或清除数据后需要执行此脚本。

用法：
  python memory/scripts/init_session_db.py          # 初始化（已存在则跳过）
  python memory/scripts/init_session_db.py --clean  # 清除后重新初始化
"""
import shutil
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger


def clean_data(data_dir: Path):
    """清除所有会话数据库文件"""
    if not data_dir.exists():
        logger.info("数据目录不存在，无需清除")
        return

    removed = 0
    for f in data_dir.iterdir():
        if f.name == ".gitkeep":
            continue
        f.unlink()
        removed += 1

    logger.info(f"已清除 {removed} 个数据文件: {data_dir}")


def init_sqlite():
    """初始化 SQLite 会话数据库"""
    from mediZJ.memory.session_db import SessionDB

    db = SessionDB()
    # 验证表是否创建成功
    conn = db._get_conn()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [t["name"] for t in tables]
    logger.info(f"SQLite 表: {table_names}")

    # 验证索引
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    index_names = [i["name"] for i in indexes]
    logger.info(f"SQLite 索引: {index_names}")

    logger.info("SQLite 会话数据库初始化完成")


def init_milvus():
    """初始化 Milvus 会话向量集合"""
    from mediZJ.memory.session_vector_store import SessionVectorStore

    store = SessionVectorStore()
    logger.info(
        f"Milvus 集合 '{store.collection_name}' 已就绪 "
        f"(维度={store.embedding_dim})"
    )
    logger.info("Milvus 会话向量索引初始化完成")


def main():
    data_dir = Path(__file__).parent.parent / "data"

    # 处理 --clean 参数
    if "--clean" in sys.argv:
        clean_data(data_dir)

    # 确保目录存在
    data_dir.mkdir(parents=True, exist_ok=True)

    # 创建 .gitkeep 保留目录结构
    gitkeep = data_dir / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()

    logger.info("=" * 50)
    logger.info("初始化会话持久化数据库")
    logger.info("=" * 50)

    init_sqlite()
    init_milvus()

    logger.info("=" * 50)
    logger.info("初始化完成！")
    logger.info(f"  SQLite: {data_dir / 'sessions.db'}")
    logger.info(f"  Milvus: {data_dir / 'session_vectors.db'}")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
