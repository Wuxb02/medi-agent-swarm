"""最终回答校验与事务持久化。"""

import re
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from mediZJ.api.models.chat import ChatRequest
from mediZJ.memory.session_db import SessionDB
from mediZJ.swarm.swarm_coordinator import SwarmCoordinator
from mediZJ.validation.medical_answer import MedicalAnswerVerifier

_session_db = SessionDB()
_answer_verifier: Optional[MedicalAnswerVerifier] = None


def _get_answer_verifier() -> MedicalAnswerVerifier:
    """按需构建最终回答校验器。"""
    global _answer_verifier
    if _answer_verifier is None:
        from mediZJ.core.llm_client import LLMClient

        semantic_enabled = os.getenv(
            "MEDICAL_SEMANTIC_VERIFY_ENABLED", "true"
        ).lower() in {"1", "true", "yes"}
        _answer_verifier = MedicalAnswerVerifier(
            llm_client=LLMClient() if semantic_enabled else None
        )
    return _answer_verifier


async def _verify_final_result(
    question: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """在持久化和输出前执行唯一的安全门。"""
    answer, verification = await _get_answer_verifier().verify_and_rewrite(
        question,
        result.get("answer", ""),
        result.get("citations", []),
    )
    answer, citations = _keep_cited_references(answer, verification.validated_citations)
    verification.validated_citations = citations
    result["answer"] = answer
    result["citations"] = citations
    result["verification"] = verification.to_dict()
    return result


def _keep_cited_references(
    answer: str,
    citations: List[Dict[str, Any]],
) -> tuple[str, List[Dict[str, Any]]]:
    """仅展示最终正文中出现过编号的参考资料。"""
    body = re.split(r"(?m)^## 参考资料[ \t]*$", answer, maxsplit=1)[0].rstrip()
    markers = re.findall(r"\[(\d+(?:[,\-]\d+)*)\]", body)
    used_indices = set()
    for marker in markers:
        for part in marker.split(","):
            if "-" in part:
                start, end = (int(number) for number in part.split("-"))
                used_indices.update(
                    citation["index"]
                    for citation in citations
                    if start <= citation["index"] <= end
                )
            else:
                used_indices.add(int(part))

    cited = [
        citation for citation in citations if citation.get("index") in used_indices
    ]
    if cited:
        body += "\n" + SwarmCoordinator.format_references_section(cited)
    return body, cited


def _merge_thinking_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并流式 thinking token，同时保留实时事件的完整结构。"""
    merged: List[Dict[str, Any]] = []
    buf_agent: Optional[str] = None
    buf_iteration: Optional[int] = None
    buf_phase: Optional[str] = None
    buf_parts: List[str] = []
    buf_envelope: Optional[Dict[str, Any]] = None
    buf_payload: Optional[Dict[str, Any]] = None

    def _flush():
        """将缓冲区中的 token 拼接为一条完整事件"""
        if not buf_parts or buf_envelope is None or buf_payload is None:
            return
        envelope = dict(buf_envelope)
        payload = dict(buf_payload)
        payload["content"] = "".join(buf_parts)
        envelope["data"] = payload
        merged.append(
            {
                "event": "agent_thinking",
                "data": envelope,
            }
        )

    for ev in events:
        if ev.get("event") != "agent_thinking":
            _flush()
            buf_parts.clear()
            buf_agent = buf_iteration = buf_phase = None
            buf_envelope = buf_payload = None
            merged.append(ev)
            continue

        data = ev.get("data", {})
        agent = data.get("source_agent")
        payload = data.get("data", data)
        iteration = payload.get("iteration")
        phase = payload.get("phase")

        if agent == buf_agent and iteration == buf_iteration and phase == buf_phase:
            # 同一轮 thinking，追加 token
            buf_parts.append(payload.get("content", ""))
            buf_payload.update(
                {key: value for key, value in payload.items() if key != "content"}
            )
        else:
            # 新的一轮 thinking，先 flush 旧的
            _flush()
            buf_agent = agent
            buf_iteration = iteration
            buf_phase = phase
            buf_parts = [payload.get("content", "")]
            buf_envelope = dict(data)
            buf_payload = dict(payload)

    _flush()
    return merged


async def _persist_session_turn(
    session_id: str,
    request: ChatRequest,
    result: Dict[str, Any],
    collected_events: List[Dict[str, Any]],
):
    """将本轮对话持久化到 SQLite，并索引到 Milvus"""
    now = datetime.now(timezone.utc).isoformat()
    turn_index = await _session_db.get_turn_count(session_id)

    # 过滤掉流式内容增量事件（与 _save_session_events 一致）
    _SKIP_TYPES = {"agent_content_delta"}
    filtered_events = [e for e in collected_events if e.get("event") not in _SKIP_TYPES]
    filtered_events = _merge_thinking_events(filtered_events)

    saved = await _session_db.save_turn(
        session_id=session_id,
        turn_index=turn_index,
        user_msg={
            "role": "user",
            "content": request.question,
            "timestamp": now,
            "images": request.images or [],
        },
        assistant_msg={
            "role": "assistant",
            "content": result.get("answer", ""),
            "timestamp": now,
            "agent_events": filtered_events,
            "suggestions": result.get("suggestions", []),
            "agents_involved": result.get("agents_involved", []),
            "total_time": result.get("total_time", 0.0),
            "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            "subtasks_completed": result.get("subtasks_completed", 0),
            "mode": "swarm" if result.get("swarm_enabled", False) else "single",
            "parallel_efficiency": result.get("performance_metrics", {}).get(
                "parallel_efficiency", 0
            ),
            "information_coverage": result.get("performance_metrics", {}).get(
                "information_coverage", 0
            ),
            "redundancy": result.get("performance_metrics", {}).get("redundancy", 0),
            "citations": result.get("citations", []),
            "trace_id": result.get("trace_id", ""),
        },
        user_id=request.user_id or "default",
    )

    from mediZJ.evolution import EvolutionService

    evolution = EvolutionService()
    await evolution.storage.record_exposures(
        int(saved["assistant_message_id"]),
        request.user_id or "default",
        result.get("experience_assignments", []),
    )
    await evolution.maybe_enqueue_sample(
        int(saved["assistant_message_id"]),
        request.user_id or "default",
    )

    from mediZJ.infrastructure.jobs import enqueue

    await enqueue(
        "session_index",
        f"session:{session_id}:{saved['turn_index']}",
        {"session_id": session_id, "user_id": request.user_id or "default"},
    )
    return saved
