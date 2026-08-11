"""会话服务：封装 SessionDB + 文件回退 + Milvus 索引"""
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from loguru import logger

from mediZJ.api.models.session import SessionListItem, SessionDetail, SessionTurn
from mediZJ.memory.session_db import SessionDB
from mediZJ.memory.session_vector_store import SessionVectorStore

# 会话总结存储目录（旧版文件存储）
SUMMARY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "mediZJ", "memory", "swarm", "session_summaries"
)

_db = SessionDB()
_vectors: Optional[SessionVectorStore] = None


def _get_session_vectors() -> SessionVectorStore:
    """按需初始化会话向量库。"""

    global _vectors
    if _vectors is None:
        _vectors = SessionVectorStore()
    return _vectors


# ─── 公开 API ──────────────────────────────────────────────


def list_sessions(
    limit: int = 50,
    offset: int = 0,
    user_id: Optional[str] = None,
) -> List[SessionListItem]:
    """列出历史会话（SQLite 分页查询）

    .md 文件不再混入分页结果，避免破坏 offset 语义。
    如需兼容旧数据，应先迁移至 SQLite。
    """
    try:
        db_sessions = _db.list_sessions(limit, offset, user_id=user_id)
        return [
            SessionListItem(
                session_id=s["session_id"],
                first_question=s.get("first_question", "")[:80],
                created_at=s.get("created_at", ""),
                message_count=s.get("message_count", 0),
                mode=s.get("mode", "single"),
                total_tokens=s.get("total_tokens", 0),
            )
            for s in db_sessions
        ]
    except Exception as e:
        logger.warning(f"Failed to list sessions from SQLite: {e}")
        return []


def count_sessions(user_id: Optional[str] = None) -> int:
    """获取会话总数"""
    try:
        return _db.count_sessions(user_id=user_id)
    except Exception as e:
        logger.warning(f"Failed to count sessions: {e}")
        return 0


def get_session_detail(
    session_id: str,
    user_id: Optional[str] = None,
) -> Optional[SessionDetail]:
    """获取会话详情（优先 SQLite，回退 .md + .json）"""
    # 1. 从 SQLite 读取
    try:
        session_data = _db.get_session(session_id, user_id=user_id)
        if session_data and session_data.get("messages"):
            return _build_detail_from_db(session_data)
    except Exception as e:
        logger.warning(f"Failed to get session from SQLite: {e}")

    # 2. 回退到 .md + .json 文件解析
    if user_id not in (None, "default"):
        return None

    for filepath in _iter_summary_files():
        if session_id in filepath and filepath.endswith(".md"):
            detail = _parse_detail_file(filepath, session_id)
            if detail:
                _merge_events_json(filepath, detail)
            return detail

    return None


def delete_session(session_id: str, user_id: Optional[str] = None) -> bool:
    """删除会话，并保持自进化经验与评审数据一致。"""
    from mediZJ.evolution.storage import EvolutionStorage

    cleanup_errors: List[str] = []

    # 1. 在同一 SQLite 事务中清理对话与自进化数据。
    try:
        evolution_storage = EvolutionStorage(_db.db_path)
        deletion = evolution_storage.delete_session_data(
            session_id,
            user_id=user_id,
        )
    except Exception as e:
        logger.warning(f"Failed to delete from SQLite: {e}")
        return False

    if deletion is None:
        return False

    # 2. 删除 Milvus 向量记录
    try:
        _get_session_vectors().delete_session(session_id)
    except Exception as e:
        logger.warning(f"Failed to delete from Milvus: {e}")
        cleanup_errors.append("会话向量清理失败")

    # 3. 删除旧版 .md 文件
    for filepath in _iter_summary_files():
        if session_id in filepath and filepath.endswith(".md"):
            try:
                os.remove(filepath)
            except OSError as e:
                logger.warning(f"Failed to delete {filepath}: {e}")
                cleanup_errors.append("旧版会话摘要清理失败")

    # 4. 删除旧版 events JSON
    events_json = os.path.join(SUMMARY_DIR, f"session_{session_id}.json")
    if os.path.exists(events_json):
        try:
            os.remove(events_json)
        except OSError as e:
            logger.warning(f"Failed to delete {events_json}: {e}")
            cleanup_errors.append("旧版会话事件清理失败")

    # 5. 记录数据库外部清理结果，不保留患者内容。
    try:
        evolution_storage.complete_session_cleanup(
            deletion["session_id_hash"],
            cleanup_errors,
        )
    except Exception as e:
        logger.warning(f"Failed to update deletion audit: {e}")

    return True


# ─── SQLite → SessionDetail 构建 ───────────────────────────


def _build_detail_from_db(session_data: Dict[str, Any]) -> SessionDetail:
    """从 SQLite 数据构建 SessionDetail（含多轮 turns）"""
    messages = session_data.get("messages", [])
    turns: List[SessionTurn] = []
    current_turn: Dict[str, Any] = {}

    for msg in messages:
        role = msg.get("role", "")
        if role == "user":
            # 如果上一轮还未结束，先保存
            if current_turn.get("user_message"):
                turns.append(SessionTurn(**current_turn))
            current_turn = {
                "turn_index": msg.get("turn_index", len(turns)),
                "user_message": {
                    "role": "user",
                    "content": msg.get("content", ""),
                    "timestamp": msg.get("timestamp", ""),
                },
                "assistant_message": {},
            }
        elif role == "assistant":
            assistant_msg = {
                "assistant_message_id": str(msg.get("id", "")),
                "role": "assistant",
                "content": msg.get("content", ""),
                "timestamp": msg.get("timestamp", ""),
                "trace_id": msg.get("trace_id", ""),
            }
            # 附加 agent_events 等字段
            for field in (
                "agent_events", "suggestions", "agents_involved",
            ):
                val = msg.get(field)
                if val:
                    assistant_msg[field] = val
            if msg.get("total_time"):
                assistant_msg["total_time"] = msg["total_time"]
            if msg.get("total_tokens"):
                assistant_msg["total_tokens"] = msg["total_tokens"]
            if msg.get("subtasks_completed"):
                assistant_msg["subtasks_completed"] = msg["subtasks_completed"]
            if msg.get("mode"):
                assistant_msg["mode"] = msg["mode"]
            # citations 可能为空列表，始终传递（排除 None 即旧会话无此字段）
            citations_val = msg.get("citations")
            if citations_val is not None:
                assistant_msg["citations"] = citations_val

            current_turn["assistant_message"] = assistant_msg

    # 最后一轮
    if current_turn.get("user_message"):
        turns.append(SessionTurn(**current_turn))

    # 构建 summary 字段（向后兼容）
    first_turn = turns[0] if turns else None
    last_turn = turns[-1] if turns else None

    agents_set = set()
    total_time = 0.0
    for t in turns:
        am = t.assistant_message
        if am.get("agents_involved"):
            agents_set.update(am["agents_involved"])
        total_time += am.get("total_time", 0)

    return SessionDetail(
        session_id=session_data["session_id"],
        question=first_turn.user_message.get("content", "") if first_turn else "",
        answer=last_turn.assistant_message.get("content", "") if last_turn else "",
        mode=session_data.get("mode", "single"),
        agents_involved=list(agents_set),
        total_time=total_time,
        created_at=session_data.get("created_at", ""),
        # 最后一轮的 events/suggestions 用于向后兼容
        agent_events=(
            last_turn.assistant_message.get("agent_events", [])
            if last_turn else []
        ),
        suggestions=(
            last_turn.assistant_message.get("suggestions", [])
            if last_turn else []
        ),
        total_tokens=session_data.get("total_tokens", 0),
        parallel_efficiency=session_data.get("parallel_efficiency", 0),
        information_coverage=session_data.get("information_coverage", 0),
        redundancy=session_data.get("redundancy", 0),
        turns=turns,
    )


# ─── 旧版文件解析（向后兼容）─────────────────────────────────


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


def _parse_summary_file(
    filepath: str, filename: str
) -> Optional[SessionListItem]:
    """解析会话总结文件为列表项"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    session_id_match = re.search(r"会话ID[：:]\s*(.+)", content)
    session_id = (
        session_id_match.group(1).strip()
        if session_id_match
        else filename.replace(".md", "")
    )

    question_match = re.search(r"原始问题[：:]\s*(.+)", content)
    first_question = question_match.group(1).strip() if question_match else ""

    mode_match = re.search(r"运行模式[：:]\s*(.+)", content)
    mode = mode_match.group(1).strip().lower() if mode_match else "single"
    if "swarm" in mode or "协作" in mode:
        mode = "swarm"
    else:
        mode = "single"

    created_at = datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()

    tokens_match = re.search(r"总 Token 消耗[：:]\s*(\d+)", content)
    total_tokens = int(tokens_match.group(1)) if tokens_match else 0

    messages_match = re.search(r"消息数[：:]\s*(\d+)", content)
    message_count = int(messages_match.group(1)) if messages_match else 0

    pe_match = re.search(r"并行效率[：:]\s*([\d.]+)%", content)
    parallel_efficiency = float(pe_match.group(1)) / 100 if pe_match else 0.0
    ic_match = re.search(r"信息覆盖度[：:]\s*([\d.]+)%", content)
    information_coverage = float(ic_match.group(1)) / 100 if ic_match else 0.0
    rd_match = re.search(r"信息冗余度[：:]\s*([\d.]+)%", content)
    redundancy = float(rd_match.group(1)) / 100 if rd_match else 0.0

    return SessionListItem(
        session_id=session_id,
        first_question=first_question[:80],
        created_at=created_at,
        message_count=message_count,
        mode=mode,
        total_tokens=total_tokens,
        parallel_efficiency=parallel_efficiency,
        information_coverage=information_coverage,
        redundancy=redundancy,
    )


def _parse_detail_file(
    filepath: str, session_id: str
) -> Optional[SessionDetail]:
    """解析会话详情（旧版 .md 文件）"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    question_match = re.search(r"原始问题[：:]\s*(.+)", content)
    question = question_match.group(1).strip() if question_match else ""

    answer_match = re.search(
        r"最终回答[：:]\s*\n([\s\S]*?)(?=\n## |\Z)", content
    )
    answer = answer_match.group(1).strip() if answer_match else ""

    mode_match = re.search(r"运行模式[：:]\s*(.+)", content)
    mode = mode_match.group(1).strip().lower() if mode_match else "single"
    mode = "swarm" if "swarm" in mode or "协作" in mode else "single"

    agents_match = re.search(r"参与 Agent[：:]\s*(.+)", content)
    agents = (
        [a.strip() for a in agents_match.group(1).split(",") if a.strip()]
        if agents_match
        else []
    )

    time_match = re.search(r"总耗时[：:]\s*([\d.]+)", content)
    total_time = float(time_match.group(1)) if time_match else 0.0

    total_tokens_match = re.search(r"总 Token 消耗[：:]\s*(\d+)", content)
    total_tokens = (
        int(total_tokens_match.group(1)) if total_tokens_match else 0
    )
    prompt_tokens_match = re.search(r"输入 Token[：:]\s*(\d+)", content)
    prompt_tokens = (
        int(prompt_tokens_match.group(1)) if prompt_tokens_match else 0
    )
    completion_tokens_match = re.search(r"输出 Token[：:]\s*(\d+)", content)
    completion_tokens = (
        int(completion_tokens_match.group(1)) if completion_tokens_match else 0
    )

    pe_match = re.search(r"并行效率[：:]\s*([\d.]+)%", content)
    parallel_efficiency = float(pe_match.group(1)) / 100 if pe_match else 0.0
    ic_match = re.search(r"信息覆盖度[：:]\s*([\d.]+)%", content)
    information_coverage = float(ic_match.group(1)) / 100 if ic_match else 0.0
    rd_match = re.search(r"信息冗余度[：:]\s*([\d.]+)%", content)
    redundancy = float(rd_match.group(1)) / 100 if rd_match else 0.0

    created_at = datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()

    return SessionDetail(
        session_id=session_id,
        question=question,
        answer=answer,
        mode=mode,
        agents_involved=agents,
        total_time=total_time,
        created_at=created_at,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        parallel_efficiency=parallel_efficiency,
        information_coverage=information_coverage,
        redundancy=redundancy,
    )


def _merge_events_json(md_filepath: str, detail: SessionDetail):
    """如果存在对应的 JSON 事件文件，将数据合并到 SessionDetail"""
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
                detail.subtasks_completed = data.get("subtasks_completed", 0)
                if data.get("answer"):
                    detail.answer = data["answer"]
                # 将 citations 附加到最后一轮 assistant_message（向后兼容）
                citations = data.get("citations", [])
                if citations and detail.turns:
                    detail.turns[-1].assistant_message["citations"] = citations
                usage = data.get("usage", {})
                if usage:
                    detail.total_tokens = usage.get(
                        "total_tokens", detail.total_tokens
                    )
                    detail.prompt_tokens = usage.get(
                        "prompt_tokens", detail.prompt_tokens
                    )
                    detail.completion_tokens = usage.get(
                        "completion_tokens", detail.completion_tokens
                    )
                metrics = data.get("performance_metrics", {})
                if metrics:
                    detail.parallel_efficiency = metrics.get(
                        "parallel_efficiency", detail.parallel_efficiency
                    )
                    detail.information_coverage = metrics.get(
                        "information_coverage", detail.information_coverage
                    )
                    detail.redundancy = metrics.get(
                        "redundancy", detail.redundancy
                    )
                logger.info(f"Merged events JSON: {json_path}")
            except Exception as e:
                logger.warning(f"Failed to read events JSON {json_path}: {e}")
            return

    # 无 JSON 文件 → 从 MD 文件重建事件数据
    try:
        with open(md_filepath, "r", encoding="utf-8") as f:
            content = f.read()
        _rebuild_events_from_md(content, detail)
    except Exception as e:
        logger.warning(f"Failed to rebuild events from MD: {e}")


def _rebuild_events_from_md(content: str, detail: SessionDetail):
    """从 Markdown 摘要重建 agent_events"""
    events: List[Dict[str, Any]] = []
    ts = detail.created_at or datetime.now().isoformat()

    agent_section = re.search(
        r"## 参与 Agent 详情\s*\n([\s\S]*?)(?=\n## |\Z)", content
    )
    if agent_section:
        agent_blocks = re.findall(
            r"### (\S+) \((\w+)\)\s*\n"
            r"- 处理子任务：(\d+) 个\s*\n"
            r"- 工具调用：(\d+) 次\s*\n"
            r"- 执行时间：([\d.]+) 秒",
            agent_section.group(1),
        )
        for agent_id, role, subtasks, tools, exec_time in agent_blocks:
            events.append(
                {
                    "event": "agent_start",
                    "data": {
                        "id": str(uuid.uuid4()),
                        "source_agent": agent_id,
                        "timestamp": ts,
                        "data": {
                            "subtask_type": role,
                            "subtasks_count": int(subtasks),
                            "tool_calls": int(tools),
                        },
                    },
                }
            )
            role_name = {
                "worker": "执行",
                "lead": "协调",
                "single": "处理",
            }.get(role, role)
            thinking_content = (
                f"作为{role_name} Agent，处理了 {subtasks} 个子任务，"
                f"调用了 {tools} 次工具，耗时 {exec_time} 秒。"
            )
            events.append(
                {
                    "event": "agent_thinking",
                    "data": {
                        "source_agent": agent_id,
                        "data": {
                            "content": thinking_content,
                            "iteration": 0,
                        },
                    },
                }
            )
            events.append(
                {
                    "event": "agent_thinking_done",
                    "data": {
                        "source_agent": agent_id,
                        "data": {
                            "iteration": 0,
                            "elapsed_seconds": float(exec_time),
                        },
                    },
                }
            )
            events.append(
                {
                    "event": "agent_complete",
                    "data": {
                        "id": str(uuid.uuid4()),
                        "source_agent": agent_id,
                        "timestamp": ts,
                        "data": {
                            "execution_time": float(exec_time),
                            "subtasks_completed": int(subtasks),
                        },
                    },
                }
            )

    collab_match = re.search(
        r"## 协作过程\s*\n\s*- 创建子任务：(\d+) 个\s*\n"
        r"- 完成子任务：(\d+) 个\s*\n- 发布事件：(\d+) 个",
        content,
    )
    if collab_match:
        detail.subtasks_completed = int(collab_match.group(2))

    findings_section = re.search(
        r"## 关键发现\s*\n([\s\S]*?)(?=\n## |\Z)", content
    )
    if findings_section:
        findings = re.findall(
            r"### (\w+)\s*\n\*\*来源\*\*:\s*(\S+)\s*\n"
            r"\*\*发现\*\*:\s*(.+)\s*\n\*\*置信度\*\*:\s*([\d.]+)%",
            findings_section.group(1),
        )
        for category, source_agent, finding, confidence in findings:
            insert_idx = len(events)
            for i in reversed(range(len(events))):
                if (
                    events[i].get("data", {}).get("source_agent")
                    == source_agent
                ):
                    insert_idx = i + 1
                    break
            events.insert(
                insert_idx,
                {
                    "event": "agent_thinking",
                    "data": {
                        "source_agent": source_agent,
                        "data": {
                            "content": (
                                f"[{category.upper()}] {finding}"
                                f"（置信度 {confidence}%）"
                            ),
                            "iteration": 1,
                        },
                    },
                },
            )

    suggestions_match = re.search(
        r"(?:【核心建议】|## 核心建议)\s*\n([\s\S]*?)(?=\n---|\n(?:## |【))", content
    )
    if suggestions_match:
        items = re.findall(
            r"\*\*\d+\.\s*(.+?)\*\*", suggestions_match.group(1)
        )
        if items:
            detail.suggestions = items[:5]

    detail.agent_events = events
    logger.info(
        f"Rebuilt {len(events)} events from MD for session {detail.session_id}"
    )
