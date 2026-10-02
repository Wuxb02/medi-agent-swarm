"""MySQL 会话查询与事务删除。"""

from typing import Any, Dict, List
from mediZJ.api.models.session import SessionListItem, SessionDetail, SessionTurn
from mediZJ.memory.session_db import SessionDB
from mediZJ.infrastructure.database import transaction
from mediZJ.infrastructure.jobs import enqueue

_db = SessionDB()


async def list_sessions(limit=50, offset=0, user_id=None):
    rows = await _db.list_sessions(limit, offset, user_id=user_id)
    return [
        SessionListItem(
            session_id=r["session_id"],
            first_question=r.get("first_question", "")[:80],
            created_at=r["created_at"],
            message_count=r["message_count"],
            mode=r["mode"],
            total_tokens=r["total_tokens"],
        )
        for r in rows
    ]


async def count_sessions(user_id=None):
    return await _db.count_sessions(user_id=user_id)


async def get_session_detail(session_id, user_id=None):
    data = await _db.get_session(session_id, user_id=user_id)
    return _build_detail_from_db(data) if data else None


async def delete_session(session_id, user_id=None):
    from mediZJ.evolution.storage import EvolutionStorage

    async with transaction() as conn:
        session = await _db.get_session(session_id, user_id=user_id)
        if session is None:
            return False
        deletion = await EvolutionStorage().delete_session_data(
            session_id, user_id=user_id
        )
        await enqueue(
            "cache_delete",
            f"cache-delete:{deletion['audit_id']}",
            {
                "session_id": session_id,
                "user_id": session["user_id"],
                "audit_id": deletion["audit_id"],
            },
        )
        await enqueue(
            "session_delete",
            f"vector-delete:{deletion['audit_id']}",
            {"session_id": session_id, "audit_id": deletion["audit_id"]},
        )
        await conn.execute(
            "UPDATE jobs SET status='cancelled',token=token+1,lease_until=NULL WHERE kind='chat' "
            "AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.run_id')) IN "
            "(SELECT run_id FROM chat_runs WHERE session_id=%s)",
            (session_id,),
        )
        await conn.execute("DELETE FROM chat_runs WHERE session_id=%s", (session_id,))
    return True


def _build_detail_from_db(session_data: Dict[str, Any]) -> SessionDetail:
    """从 MySQL 数据构建 SessionDetail（含多轮 turns）"""
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
                "agent_events",
                "suggestions",
                "agents_involved",
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
            last_turn.assistant_message.get("agent_events", []) if last_turn else []
        ),
        suggestions=(
            last_turn.assistant_message.get("suggestions", []) if last_turn else []
        ),
        total_tokens=session_data.get("total_tokens", 0),
        parallel_efficiency=session_data.get("parallel_efficiency", 0),
        information_coverage=session_data.get("information_coverage", 0),
        redundancy=session_data.get("redundancy", 0),
        turns=turns,
    )
