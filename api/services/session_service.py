"""会话服务：封装 SessionSummaryManager + ShortTermMemory"""
import os
import re
from typing import List, Optional
from datetime import datetime
from loguru import logger

from api.models.session import SessionListItem, SessionDetail

# 会话总结存储目录
SUMMARY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "memory", "swarm", "session_summaries")


def _iter_summary_files() -> List[str]:
    """递归获取所有 .md 文件（兼容旧的日期子目录结构）"""
    if not os.path.exists(SUMMARY_DIR):
        return []
    result = []
    for dirpath, _, filenames in os.walk(SUMMARY_DIR):
        for fn in filenames:
            if fn.endswith(".md"):
                result.append(os.path.join(dirpath, fn))
    return result


def list_sessions(limit: int = 50) -> List[SessionListItem]:
    """列出历史会话"""
    sessions = []
    for filepath in _iter_summary_files():
        filename = os.path.basename(filepath)
        try:
            session = _parse_summary_file(filepath, filename)
            if session:
                sessions.append(session)
        except Exception as e:
            logger.warning(f"Failed to parse {filename}: {e}")
    # 按修改时间倒序
    sessions.sort(key=lambda s: s.created_at, reverse=True)
    return sessions[:limit]


def get_session_detail(session_id: str) -> Optional[SessionDetail]:
    """获取会话详情"""
    for filepath in _iter_summary_files():
        if session_id in filepath and filepath.endswith(".md"):
            return _parse_detail_file(filepath, session_id)
    return None


def delete_session(session_id: str) -> bool:
    """删除会话"""
    for filepath in _iter_summary_files():
        if session_id in filepath and filepath.endswith(".md"):
            os.remove(filepath)
            return True
    return False


def _parse_summary_file(filepath: str, filename: str) -> Optional[SessionListItem]:
    """解析会话总结文件为列表项"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 提取 session_id
    session_id_match = re.search(r"会话ID[：:]\s*(.+)", content)
    session_id = session_id_match.group(1).strip() if session_id_match else filename.replace(".md", "")

    # 提取问题
    question_match = re.search(r"原始问题[：:]\s*(.+)", content)
    first_question = question_match.group(1).strip() if question_match else ""

    # 提取模式
    mode_match = re.search(r"运行模式[：:]\s*(.+)", content)
    mode = mode_match.group(1).strip().lower() if mode_match else "single"
    if "swarm" in mode or "协作" in mode:
        mode = "swarm"
    else:
        mode = "single"

    # 文件修改时间
    created_at = datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()

    return SessionListItem(
        session_id=session_id,
        first_question=first_question[:80],
        created_at=created_at,
        message_count=0,
        mode=mode
    )


def _parse_detail_file(filepath: str, session_id: str) -> Optional[SessionDetail]:
    """解析会话详情"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    question_match = re.search(r"原始问题[：:]\s*(.+)", content)
    question = question_match.group(1).strip() if question_match else ""

    answer_match = re.search(r"最终回答[：:]\s*\n([\s\S]+?)\n---", content)
    if not answer_match:
        answer_match = re.search(r"最终回答[：:]\s*\n([\s\S]+?)\n## 性能指标", content)
    if not answer_match:
        answer_match = re.search(r"最终回答[：:]\s*\n([\s\S]+?)\Z", content)
    answer = answer_match.group(1).strip() if answer_match else ""

    mode_match = re.search(r"运行模式[：:]\s*(.+)", content)
    mode = mode_match.group(1).strip().lower() if mode_match else "single"
    mode = "swarm" if "swarm" in mode or "协作" in mode else "single"

    agents_match = re.search(r"参与 Agent[：:]\s*(.+)", content)
    agents = [a.strip() for a in agents_match.group(1).split(",") if a.strip()] if agents_match else []

    time_match = re.search(r"总耗时[：:]\s*([\d.]+)", content)
    total_time = float(time_match.group(1)) if time_match else 0.0

    created_at = datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()

    return SessionDetail(
        session_id=session_id,
        question=question,
        answer=answer,
        mode=mode,
        agents_involved=agents,
        total_time=total_time,
        created_at=created_at
    )
