"""医疗记忆统一上下文构建器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .prompt_prefix import GLOBAL_PREFIX_VERSION, PromptPrefixAssembler, stable_hash
from .short_term import ShortTermMemory
from .structured_memory import StructuredMemoryStore


@dataclass
class MedicalMemoryContext:
    global_static_prefix: str
    user_stable_prefix: str
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    session_summary: str = ""
    resolved_entities: dict[str, Any] = field(default_factory=dict)
    user_memories: list[dict[str, Any]] = field(default_factory=list)
    episodic_memories: list[dict[str, Any]] = field(default_factory=list)
    evidence_chunks: list[dict[str, Any]] = field(default_factory=list)
    procedural_strategies: str = ""
    used_memory_ids: list[str] = field(default_factory=list)
    global_prefix_hash: str = ""
    profile_prefix_hash: str = ""
    global_prefix_version: str = GLOBAL_PREFIX_VERSION
    profile_revision: int = 0
    session_id: str = ""
    user_id: str = ""
    agent_id: str = ""
    call_type: str = ""
    query: str = ""
    collected_info: str = ""

    def prompt_messages(self, question: Optional[str] = None) -> list[dict[str, str]]:
        """按 KV cache 友好顺序输出消息。"""
        messages = [{"role": "system", "content": self.global_static_prefix}]
        if self.user_stable_prefix:
            messages.append({"role": "system", "content": self.user_stable_prefix})
        messages.extend(
            {
                "role": str(message.get("role", "")),
                "content": str(message.get("content", "")),
            }
            for message in self.recent_messages
            if message.get("role") in {"user", "assistant"}
            and message.get("content")
        )
        dynamic = self._dynamic_text(question or self.query)
        if dynamic:
            messages.append({"role": "user", "content": dynamic})
        return messages

    def for_lead_agent(self) -> dict[str, Any]:
        return self._consumer_context()

    def for_worker(self, agent_id: str) -> dict[str, Any]:
        context = self._consumer_context()
        context["agent_id"] = agent_id
        return context

    async def record_usage(
        self, store: StructuredMemoryStore, trace_id: str
    ) -> None:
        store.record_usage(
            self.used_memory_ids,
            session_id=self.session_id,
            trace_id=trace_id,
            agent_id=self.agent_id,
            user_id=self.user_id,
        )

    def _consumer_context(self) -> dict[str, Any]:
        return {
            "memory_context": self,
            "personal_profile": self.user_stable_prefix,
            "recent_history": self.recent_messages,
            "historical_cases": self.episodic_memories,
            "collected_info": self.collected_info,
            "verified_experiences": self.procedural_strategies,
        }

    def _dynamic_text(self, question: str) -> str:
        sections = []
        if self.collected_info:
            sections.append("## 本轮已确认信息\n" + self.collected_info)
        if self.resolved_entities:
            entities = "\n".join(
                f"{key}：{self.resolved_entities[key]}"
                for key in sorted(self.resolved_entities)
            )
            sections.append("## 已解析实体\n" + entities)
        if self.episodic_memories:
            summaries = "\n".join(
                f"- {item['summary']}" for item in self.episodic_memories
            )
            sections.append("## 历史情景摘要（不可作为医学证据）\n" + summaries)
        if self.evidence_chunks:
            evidence = "\n".join(
                f"- {item.get('content') or item.get('snippet') or ''}"
                for item in self.evidence_chunks
            )
            sections.append("## 本轮医学证据\n" + evidence)
        if self.procedural_strategies:
            sections.append(
                "## 已验证执行策略（不可作为医学证据）\n"
                + self.procedural_strategies
            )
        if question:
            sections.append("## 当前任务\n" + question)
        return "\n\n".join(sections)


class MedicalMemoryContextBuilder:
    """执行权限、状态、时效与稳定前缀组装。"""

    def __init__(
        self,
        store: Optional[StructuredMemoryStore] = None,
        working_memory: Optional[ShortTermMemory] = None,
    ) -> None:
        self.store = store or StructuredMemoryStore()
        self.working_memory = working_memory or ShortTermMemory()

    async def build(
        self,
        *,
        session_id: str,
        user_id: str,
        query: str,
        agent_id: str,
        call_type: str,
        base_system_prompt: str,
        collected_info: str = "",
        evidence_chunks: Optional[list[dict[str, Any]]] = None,
        verified_experiences: str = "",
        resolved_entities: Optional[dict[str, Any]] = None,
        include_history: bool = True,
    ) -> MedicalMemoryContext:
        user_memories = [
            item
            for item in self.store.list_items(user_id, statuses=("active",))
            if item["consent_scope"] == "personalization"
            and (
                item["sensitivity_level"] != "highly_sensitive"
                or item["consent_scope"] == "personalization"
            )
        ]
        user_prefix = PromptPrefixAssembler.user_prefix(user_memories)
        global_prefix = PromptPrefixAssembler.global_prefix(base_system_prompt)
        profile_hash = stable_hash(user_prefix)
        self.store.set_profile_hash(user_id, profile_hash)
        recent = []
        if include_history:
            recent = await self.working_memory.get_recent_messages(
                session_id=session_id, limit=None
            )
        episodes = self.store.recall_episodes(user_id, session_id)
        return MedicalMemoryContext(
            global_static_prefix=global_prefix,
            user_stable_prefix=user_prefix,
            recent_messages=recent,
            resolved_entities=resolved_entities or {},
            user_memories=user_memories,
            episodic_memories=episodes,
            evidence_chunks=evidence_chunks or [],
            procedural_strategies=verified_experiences,
            used_memory_ids=[item["memory_id"] for item in user_memories],
            global_prefix_hash=stable_hash(global_prefix),
            profile_prefix_hash=profile_hash,
            profile_revision=self.store.get_profile_revision(user_id),
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            call_type=call_type,
            query=query,
            collected_info=collected_info,
        )
