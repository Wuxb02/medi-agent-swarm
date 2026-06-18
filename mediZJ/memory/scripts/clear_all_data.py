"""
清空所有会话 & Trace 数据

清理范围：
  - SQLite sessions/messages 表（会话记录）
  - SQLite traces/spans 表（Trace 数据）
  - Milvus session_vectors.db（会话向量索引，重启后自动重建）
  - memory/swarm/session_summaries/（旧版 .md/.json 文件）

用法: python memory/scripts/clear_all_data.py
"""

import os
import sys
import sqlite3

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DB = os.path.join(ROOT, "mediZJ", "memory", "data", "sessions.db")
MV_DB = os.path.join(ROOT, "mediZJ", "memory", "data", "session_vectors.db")
MD_DIR = os.path.join(ROOT, "mediZJ", "memory", "swarm", "session_summaries")


def clear_sqlite():
    if not os.path.exists(DB):
        print(f"[SKIP] SQLite 数据库不存在: {DB}")
        return

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript("""
        DELETE FROM spans;
        DELETE FROM traces;
        DELETE FROM messages;
        DELETE FROM sessions;
        DELETE FROM sqlite_sequence;
    """)
    conn.commit()
    conn.close()
    print("[OK] SQLite: sessions / messages / traces / spans 已清空")


def clear_milvus():
    if not os.path.exists(MV_DB):
        print(f"[SKIP] Milvus 向量库不存在: {MV_DB}")
        return

    os.remove(MV_DB)
    print(f"[OK] Milvus: {MV_DB} 已删除（重启 API 后自动重建）")


def clear_old_files():
    if not os.path.exists(MD_DIR):
        print(f"[SKIP] 旧版文件目录不存在: {MD_DIR}")
        return

    removed = 0
    for f in os.listdir(MD_DIR):
        fpath = os.path.join(MD_DIR, f)
        if os.path.isfile(fpath):
            os.remove(fpath)
            removed += 1
    print(f"[OK] 旧版文件: {removed} 个 .md/.json 已删除")


def main():
    print("=" * 50)
    print("清空所有会话 & Trace 数据")
    print("=" * 50)

    clear_sqlite()
    clear_milvus()
    clear_old_files()

    print("=" * 50)
    print("全部清空完成。")
    print("=" * 50)


if __name__ == "__main__":
    main()