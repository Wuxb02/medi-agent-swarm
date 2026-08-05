"""test_swarm/test_tool_executor.py — 工具执行节点回归测试

验证 LangGraph 问卷链路的消息配对修复：
- question_for_user 工具产生 questionnaire_pending 且不生成 tool 消息
  （防止暂停节点恢复前出现未配对的 assistant.tool_calls）
- 普通工具正常生成带 tool_call_id 的 tool 消息
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mediZJ.lgraph.tool_executor import make_tool_execution_node, _extract_tool_calls


def _make_questionnaire_tool():
    """返回模拟的 question_for_user 工具，返回 needs_user_input 标记。"""
    return AsyncMock(return_value={
        "needs_user_input": True,
        "questionnaire_id": "q-123",
        "questionnaire_data": {"questions": []},
        "_questions_ref": [],
    })


def _make_normal_tool():
    return AsyncMock(return_value={"success": True, "content": "知识库结果"})


class TestToolExecutionNode:
    @pytest.mark.asyncio
    async def test_questionnaire_tool_sets_pending_without_tool_message(self):
        """问卷工具：设置 questionnaire_pending（含 tool_call_id），且不生成 tool 消息。"""
        registry = MagicMock()
        registry.execute = _make_questionnaire_tool()

        node = make_tool_execution_node(tool_registry=registry)
        state = {
            "agent_id": "consultation_agent",
            "messages": [{
                "role": "assistant",
                "tool_calls": [{
                    "id": "call_q1",
                    "type": "function",
                    "function": {
                        "name": "question_for_user",
                        "arguments": json.dumps({"questionnaire": "<questions/>"}),
                    },
                }],
            }],
        }

        result = await node(state)

        pending = result["questionnaire_pending"]
        assert pending["id"] == "q-123"
        assert pending["tool_call_id"] == "call_q1"  # 关键：携带真实 tool_call_id
        # 关键：不生成 tool 消息（暂停节点恢复后由真实 id 配对）
        assert result["messages"] == []
        # question_for_user 不计入 tool_call_count（同 activate_skill 系统交互豁免）
        assert result["tool_call_count"] == 0

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

        assert result["questionnaire_pending"] is None
        assert len(result["messages"]) == 1
        msg = result["messages"][0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "call_x1"
        assert msg["name"] == "search_knowledge"
        assert result["tool_call_count"] == 1

    @pytest.mark.asyncio
    async def test_questionnaire_tool_failure_skips_pending_and_generates_message(self):
        """问卷工具执行失败（无 needs_user_input）：不进入 pending，正常生成 tool 消息。"""
        registry = MagicMock()
        registry.execute = AsyncMock(return_value={
            "success": False,
            "error": "问卷解析失败",
        })

        node = make_tool_execution_node(tool_registry=registry)
        state = {
            "agent_id": "consultation_agent",
            "messages": [{
                "role": "assistant",
                "tool_calls": [{
                    "id": "call_q2",
                    "type": "function",
                    "function": {
                        "name": "question_for_user",
                        "arguments": "{}",
                    },
                }],
            }],
        }

        result = await node(state)

        assert result["questionnaire_pending"] is None
        assert len(result["messages"]) == 1
        assert result["messages"][0]["tool_call_id"] == "call_q2"
        # question_for_user 不计入 tool_call_count（失败也不消耗配额）
        assert result["tool_call_count"] == 0


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
