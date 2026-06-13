"""test/helpers.py — 测试辅助函数（非 fixtures，可直接 import）"""

import json
from unittest.mock import MagicMock


def make_mock_openai_response(content="test response", finish_reason="stop",
                              tool_calls=None, reasoning_content=None, usage=None):
    """构造完整的模拟 OpenAI ChatCompletion 对象（用于 _parse_response 测试）。"""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].finish_reason = finish_reason
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = content

    mock_tool_calls = []
    if tool_calls:
        for tc in tool_calls:
            mock_tc = MagicMock()
            mock_tc.id = tc.get("id", "call_1")
            mock_tc.function = MagicMock()
            mock_tc.function.name = tc.get("name", "test_tool")
            mock_tc.function.arguments = tc.get("arguments", "{}")
            if isinstance(mock_tc.function.arguments, dict):
                mock_tc.function.arguments = json.dumps(mock_tc.function.arguments)
            mock_tool_calls.append(mock_tc)
    response.choices[0].message.tool_calls = mock_tool_calls or None

    if reasoning_content:
        response.choices[0].message.reasoning_content = reasoning_content
    else:
        type(response.choices[0].message).reasoning_content = property(lambda self: None)

    if usage:
        response.usage = MagicMock()
        response.usage.prompt_tokens = usage.get("prompt_tokens", 10)
        response.usage.completion_tokens = usage.get("completion_tokens", 20)
        response.usage.total_tokens = usage.get("total_tokens", 30)
    else:
        type(response).usage = property(lambda self: None)

    return response
