"""test_swarm/test_tool_executor.py — 工具执行节点回归测试

验证 LangGraph 工具执行节点的消息配对：
- 普通工具正常生成带 tool_call_id 的 tool 消息（与 assistant.tool_calls 配对）
- 问卷工具（question_for_user）已从 Worker 子图移除，仅 LeadAgent clarify 使用，
  不再由工具执行节点检测（见 supervisor_graph._clarify）
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.lgraph.tool_executor import make_tool_execution_node, _extract_tool_calls


def _make_normal_tool():
    return AsyncMock(return_value={"success": True, "content": "知识库结果"})


class TestToolExecutionNode:
    @pytest.mark.asyncio
    async def test_normal_tool_generates_paired_tool_message(self):
        """普通工具：生成带 tool_call_id 的 tool 消息（与 assistant.tool_calls 配对）。"""
        registry = MagicMock()
        registry.execute = _make_normal_tool()

        node = make_tool_execution_node(tool_registry=registry)
        state = {
            "agent_id": "consultation_agent",
            "messages": [{
                "role": "assistant",
                "tool_calls": [{
                    "id": "call_x1",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge",
                        "arguments": "{}",
                    },
                }],
            }],
        }

        result = await node(state)

        assert len(result["messages"]) == 1
        msg = result["messages"][0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_x1"
        assert msg["name"] == "search_knowledge"
        assert result["tool_call_count"] == 1


class TestExtractToolCalls:
    """_extract_tool_calls 兼容性（LangChain AIMessage / dict 两种格式）。"""

    def test_extracts_from_dict(self):
        last = {
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "foo", "arguments": '{"a": 1}'},
            }],
        }
        calls = _extract_tool_calls(last)
        assert len(calls) == 1
        assert calls[0].id == "c1"
        assert calls[0].name == "foo"
        assert calls[0].arguments == {"a": 1}

    def test_no_tool_calls_returns_empty(self):
        assert _extract_tool_calls({"role": "assistant", "content": "hi"}) == []

    def test_extracts_from_langchain_ai_message(self):
        ai_message = MagicMock()
        ai_message.tool_calls = [{
            "id": "c2",
            "name": "bar",
            "args": {"b": 2},
            "type": "tool_call",
        }]
        calls = _extract_tool_calls(ai_message)
        assert len(calls) == 1
        assert calls[0].id == "c2"
        assert calls[0].name == "bar"
        assert calls[0].arguments == {"b": 2}
