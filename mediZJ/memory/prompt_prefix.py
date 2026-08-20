"""KV cache 优先的确定性提示词前缀组装。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional


GLOBAL_PREFIX_VERSION = "memory-v1"


def stable_hash(value: str) -> str:
    """返回 UTF-8 文本的 SHA-256 指纹。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonicalize(value: Any) -> Any:
    """递归规范化可序列化数据。"""
    if isinstance(value, dict):
        return {str(key): canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (set, frozenset)):
        return [canonicalize(item) for item in sorted(value, key=str)]
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """使用固定配置生成 JSON。"""
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_tools(tools: Optional[Iterable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """按工具名称和固定键顺序输出 tool schema。"""
    normalized = [canonicalize(tool) for tool in (tools or [])]
    return sorted(
        normalized,
        key=lambda tool: str(tool.get("function", {}).get("name", "")),
    )


class PromptPrefixAssembler:
    """组装字节稳定的全局前缀与用户画像前缀。"""

    SAFETY_POLICY = (
        "## 记忆与证据规则\n"
        "医学知识证据的权威级别高于用户画像与会话记忆。\n"
        "用户画像、用户自述、情景摘要和程序性策略不得作为医学引用。"
    )

    @classmethod
    def global_prefix(
        cls,
        base_system_prompt: str,
        *,
        output_protocol: str = "",
    ) -> str:
        sections = [base_system_prompt.strip(), cls.SAFETY_POLICY]
        if output_protocol.strip():
            sections.append(output_protocol.strip())
        return "\n\n".join(section for section in sections if section)

    @staticmethod
    def user_prefix(user_memories: list[dict[str, Any]]) -> str:
        if not user_memories:
            return ""
        ordered = sorted(
            user_memories,
            key=lambda item: (
                item["memory_type"],
                item["memory_key"],
                item["memory_id"],
            ),
        )
        facts = []
        records = []
        for item in ordered:
            if item["memory_type"] == "profile_fact":
                facts.append(f"{item['memory_key']}：{item['value']}")
            else:
                value = item["value"]
                parts = [
                    str(value.get("description", "")),
                    str(value.get("symptoms", "")),
                    str(value.get("duration", "")),
                    str(value.get("medication", "")),
                    str(value.get("outcome", "")),
                ]
                detail = "，".join(part for part in parts if part)
                records.append(f"[{value.get('date', '')}] {detail}")
        sections = []
        if facts:
            sections.append("## 用户已确认资料\n" + "\n".join(facts))
        if records:
            sections.append("## 用户已确认病史\n" + "\n".join(records))
        return "\n\n".join(sections)

