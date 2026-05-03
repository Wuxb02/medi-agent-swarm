"""会话服务：封装 SessionSummaryManager + ShortTermMemory"""
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional
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
            detail = _parse_detail_file(filepath, session_id)
            if detail:
                _merge_events_json(filepath, detail)
            return detail
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

    # 提取最终回答：匹配到下一个 ## 章节或文件末尾（不使用 --- 分隔符，因内容中可能含 ---）
    answer_match = re.search(r"最终回答[：:]\s*\n([\s\S]*?)(?=\n## |\Z)", content)
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


def _merge_events_json(md_filepath: str, detail: SessionDetail):
    """如果存在对应的 JSON 事件文件，将数据合并到 SessionDetail；否则从 MD 重建"""
    # 尝试两种 JSON 文件名格式：session_{id}.json 和 {id}.json
    base_dir = os.path.dirname(md_filepath)
    basename = os.path.splitext(os.path.basename(md_filepath))[0]
    candidates = [
        os.path.join(base_dir, f"session_{basename}.json"),
        os.path.join(base_dir, f"{basename}.json"),
    ]
    for json_path in candidates:
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                detail.agent_events = data.get("events", [])
                detail.suggestions = data.get("suggestions", [])
                detail.disclaimer = data.get("disclaimer", "")
                detail.subtasks_completed = data.get("subtasks_completed", 0)
                # 如果 JSON 中有更准确的 answer，优先使用
                if data.get("answer"):
                    detail.answer = data["answer"]
                logger.info(f"Merged events JSON: {json_path}")
            except Exception as e:
                logger.warning(f"Failed to read events JSON {json_path}: {e}")
            return

    # 无 JSON 文件 → 从 MD 文件重建事件数据（兼容旧会话）
    try:
        with open(md_filepath, "r", encoding="utf-8") as f:
            content = f.read()
        _rebuild_events_from_md(content, detail)
    except Exception as e:
        logger.warning(f"Failed to rebuild events from MD: {e}")


def _rebuild_events_from_md(content: str, detail: SessionDetail):
    """从 Markdown 摘要重建 agent_events，为旧会话提供历史展示数据"""
    events: List[Dict[str, Any]] = []
    ts = detail.created_at or datetime.now().isoformat()

    # 解析参与 Agent 详情 → 生成 agent_start / thinking / agent_complete 事件
    agent_section = re.search(
        r"## 参与 Agent 详情\s*\n([\s\S]*?)(?=\n## |\Z)", content
    )
    if agent_section:
        agent_blocks = re.findall(
            r"### (\S+) \((\w+)\)\s*\n"
            r"- 处理子任务：(\d+) 个\s*\n"
            r"- 工具调用：(\d+) 次\s*\n"
            r"- 执行时间：([\d.]+) 秒",
            agent_section.group(1)
        )
        for agent_id, role, subtasks, tools, exec_time in agent_blocks:
            # agent_start
            events.append({
                "event": "agent_start",
                "data": {
                    "id": str(uuid.uuid4()),
                    "source_agent": agent_id,
                    "timestamp": ts,
                    "data": {
                        "subtask_type": role,
                        "subtasks_count": int(subtasks),
                        "tool_calls": int(tools),
                    }
                }
            })
            # thinking（从 Agent 详情重建执行摘要）
            role_name = {"worker": "执行", "lead": "协调", "single": "处理"}.get(role, role)
            thinking_content = (
                f"作为{role_name} Agent，处理了 {subtasks} 个子任务，"
                f"调用了 {tools} 次工具，耗时 {exec_time} 秒。"
            )
            events.append({
                "event": "agent_thinking",
                "data": {
                    "source_agent": agent_id,
                    "data": {
                        "content": thinking_content,
                        "iteration": 0,
                    }
                }
            })
            events.append({
                "event": "agent_thinking_done",
                "data": {
                    "source_agent": agent_id,
                    "data": {
                        "iteration": 0,
                        "elapsed_seconds": float(exec_time),
                    }
                }
            })
            # agent_complete
            events.append({
                "event": "agent_complete",
                "data": {
                    "id": str(uuid.uuid4()),
                    "source_agent": agent_id,
                    "timestamp": ts,
                    "data": {
                        "execution_time": float(exec_time),
                        "subtasks_completed": int(subtasks),
                    }
                }
            })

    # 解析协作过程 → 写入 metadata
    collab_match = re.search(
        r"## 协作过程\s*\n\s*- 创建子任务：(\d+) 个\s*\n- 完成子任务：(\d+) 个\s*\n- 发布事件：(\d+) 个",
        content
    )
    if collab_match:
        detail.subtasks_completed = int(collab_match.group(2))

    # 解析关键发现 → 追加 thinking 事件（补充到对应 Agent）
    findings_section = re.search(r"## 关键发现\s*\n([\s\S]*?)(?=\n## |\Z)", content)
    if findings_section:
        findings = re.findall(
            r"### (\w+)\s*\n\*\*来源\*\*:\s*(\S+)\s*\n\*\*发现\*\*:\s*(.+)\s*\n\*\*置信度\*\*:\s*([\d.]+)%",
            findings_section.group(1)
        )
        for category, source_agent, finding, confidence in findings:
            # 插入到该 Agent 的最后一个 thinking_done 之前
            insert_idx = len(events)
            for i in reversed(range(len(events))):
                if events[i].get("data", {}).get("source_agent") == source_agent:
                    insert_idx = i + 1
                    break
            events.insert(insert_idx, {
                "event": "agent_thinking",
                "data": {
                    "source_agent": source_agent,
                    "data": {
                        "content": f"[{category.upper()}] {finding}（置信度 {confidence}%）",
                        "iteration": 1,
                    }
                }
            })

    # 解析免责声明
    disclaimer_match = re.search(r"【免责声明】\s*\n(.+?)(?=\n---|\n## |\Z)", content, re.DOTALL)
    if disclaimer_match:
        detail.disclaimer = disclaimer_match.group(1).strip()

    # 解析核心建议 → suggestions
    suggestions_match = re.search(r"【核心建议】\s*\n([\s\S]*?)(?=\n---|\n【)", content)
    if suggestions_match:
        items = re.findall(r"\*\*\d+\.\s*(.+?)\*\*", suggestions_match.group(1))
        if items:
            detail.suggestions = items[:5]

    detail.agent_events = events
    logger.info(f"Rebuilt {len(events)} events from MD for session {detail.session_id}")
