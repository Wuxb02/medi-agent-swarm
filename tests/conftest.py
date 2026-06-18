# test/conftest.py - 共享 fixtures、markers、pytest 配置

import asyncio
import os
import tempfile
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================
# 环境变量（autouse，确保 LLMClient 等不因缺 env 崩溃）
# 集成测试使用真实 .env 配置，单元测试注入伪变量
# ============================================================

@pytest.fixture(autouse=True)
def setup_env(request, monkeypatch):
    """单元测试注入伪环境变量；集成测试保留真实 .env 配置。"""
    if request.node.get_closest_marker("integration"):
        return  # 集成测试使用真实 .env
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://test-api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.0")
    monkeypatch.setenv("LLM_MAX_TOKENS", "100")
    monkeypatch.setenv("MEM0_API_KEY", "test-mem0-key")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5")


# ============================================================
# Event Loop
# ============================================================

@pytest.fixture(scope="session")
def event_loop():
    """Session 级别 event loop，pytest-asyncio 自动识别。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ============================================================
# Mock LLMClient 工厂
# ============================================================

@pytest.fixture
def mock_llm_client():
    """返回 LLMClient，其底层 AsyncOpenAI 被完全 mock。

    测试可通过 client.client.chat.completions.create.return_value
    或 .side_effect 控制返回值。
    """
    with patch("mediZJ.core.llm_client.AsyncOpenAI", autospec=True) as mock_openai_cls:
        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance
        # 设置默认属性
        mock_instance.base_url = "https://test-api.example.com/v1"

        from mediZJ.core.llm_client import LLMClient
        client = LLMClient()
        client.client = mock_instance
        yield client


def make_llm_response(content=None, tool_calls=None, finish_reason="stop",
                      reasoning_content=None, usage=None):
    """快速构造 LLMResponse 对象。"""
    from mediZJ.core.llm_client import LLMResponse
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        reasoning_content=reasoning_content,
        usage=usage,
    )


def make_openai_chunk(content="", finish_reason=None, tool_call_delta=None,
                      reasoning_content=None, usage=None):
    """构造模拟的 OpenAI 流式 chunk 对象。"""
    chunk = MagicMock()
    chunk.choices = []
    if content or finish_reason or tool_call_delta:
        choice = MagicMock()
        choice.finish_reason = finish_reason
        choice.delta = MagicMock()
        choice.delta.content = content
        choice.delta.tool_calls = None
        if tool_call_delta:
            choice.delta.tool_calls = tool_call_delta
        if reasoning_content:
            choice.delta.reasoning_content = reasoning_content
        else:
            type(choice.delta).reasoning_content = property(lambda self: None)
        chunk.choices = [choice]

    if usage:
        chunk.usage = MagicMock()
        chunk.usage.prompt_tokens = usage.get("prompt_tokens", 0)
        chunk.usage.completion_tokens = usage.get("completion_tokens", 0)
        chunk.usage.total_tokens = usage.get("total_tokens", 0)
    else:
        type(chunk).usage = property(lambda self: None)

    return chunk


def make_mock_openai_response(content="test response", finish_reason="stop",
                              tool_calls=None, reasoning_content=None, usage=None):
    """构造完整的模拟 OpenAI ChatCompletion 对象（用于 _parse_response）。"""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].finish_reason = finish_reason
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = content

    mock_tool_calls = []
    if tool_calls:
        for tc in tool_calls:
            import json
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


# ============================================================
# 临时目录
# ============================================================

@pytest.fixture
def temp_dir():
    """提供临时目录，测试结束后自动清理。"""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


# ============================================================
# Mock Embedding
# ============================================================

@pytest.fixture
def mock_embedding():
    """返回 stub embedding 模型，固定返回 512 维向量。"""
    import numpy as np
    stub = MagicMock()
    stub.encode = MagicMock(return_value=np.array([0.1] * 512))
    return stub


# ============================================================
# ShortTermMemory（隔离的，每次测试重置单例）
# ============================================================

@pytest.fixture
def short_term_memory():
    """提供隔离的 ShortTermMemory 实例（内存后端）。"""
    from mediZJ.memory.short_term import ShortTermMemory
    # 重置单例
    ShortTermMemory._instance = None
    stm = ShortTermMemory(storage_type="memory")
    yield stm
    # 清理
    if hasattr(stm, "_sessions"):
        stm._sessions.clear()


# ============================================================
# ConstraintValidator
# ============================================================

@pytest.fixture
def constraint_validator():
    """提供 ConstraintValidator 实例。"""
    from mediZJ.constraints.validator import ConstraintValidator
    return ConstraintValidator()


# ============================================================
# AutoFixer
# ============================================================

@pytest.fixture
def auto_fixer():
    """提供 AutoFixer 实例。"""
    from mediZJ.validation.auto_fixer import AutoFixer
    return AutoFixer()


# ============================================================
# TraceCollector 隔离
# ============================================================

@pytest.fixture(autouse=True)
def reset_trace_collector():
    """每个测试前后重置 TraceCollector 单例。"""
    from mediZJ.trace.collector import TraceCollector
    TraceCollector.reset()
    yield
    TraceCollector.reset()


# ============================================================
# Trace Context 隔离
# ============================================================

@pytest.fixture(autouse=True)
def reset_trace_context():
    """每个测试前后清除 trace contextvars，防止测试间泄漏。"""
    from mediZJ.trace.context import _current_trace_id, _current_span_stack
    # 保存原始值
    old_trace_id = _current_trace_id.get()
    old_stack = _current_span_stack.get()
    yield
    # 恢复
    _current_trace_id.set(old_trace_id)
    _current_span_stack.set(old_stack)


# ============================================================
# pytest 配置 hooks
# ============================================================

def pytest_configure(config):
    """注册自定义 markers。"""
    config.addinivalue_line("markers", "unit: 纯单元测试，无外部依赖")
    config.addinivalue_line("markers", "integration: 需要外部服务 (LLM/Mem0/Milvus/网络)")
    config.addinivalue_line("markers", "slow: 慢速测试 (真实 LLM 调用)")


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require external services",
    )


def pytest_collection_modifyitems(config, items):
    """默认跳过 integration 标记的测试，除非传了 --run-integration。"""
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(reason="需要 --run-integration 标志才能运行集成测试")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_integration)
