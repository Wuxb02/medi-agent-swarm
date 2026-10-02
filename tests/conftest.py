# test/conftest.py - 共享 fixtures、markers、pytest 配置

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# 环境变量（autouse，确保 LLMClient 等不因缺 env 崩溃）
# 集成测试使用真实 .env 配置，单元测试注入伪变量
# ============================================================


@pytest.fixture(autouse=True)
def setup_env(request, monkeypatch):
    """单元测试注入伪环境变量；集成测试保留真实 .env 配置。"""
    if request.node.get_closest_marker(
        "integration"
    ) and not request.node.get_closest_marker("infrastructure"):
        return  # 真实模型测试使用 .env
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://test-api.example.com/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.0")
    monkeypatch.setenv("LLM_MAX_TOKENS", "100")
    monkeypatch.setenv("MEDICAL_SEMANTIC_VERIFY_ENABLED", "false")
    monkeypatch.setenv("EVOLUTION_GLOBAL_MIN_SUPPORT", "3")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5")


# ============================================================
# Event Loop
# ============================================================

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


def make_llm_response(
    content=None,
    tool_calls=None,
    finish_reason="stop",
    reasoning_content=None,
    usage=None,
):
    """快速构造 LLMResponse 对象。"""
    from mediZJ.core.llm_client import LLMResponse

    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        reasoning_content=reasoning_content,
        usage=usage,
    )


def make_openai_chunk(
    content="",
    finish_reason=None,
    tool_call_delta=None,
    reasoning_content=None,
    usage=None,
):
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


def make_mock_openai_response(
    content="test response",
    finish_reason="stop",
    tool_calls=None,
    reasoning_content=None,
    usage=None,
):
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
        type(response.choices[0].message).reasoning_content = property(
            lambda self: None
        )

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
async def short_term_memory(mysql_infrastructure):
    """提供使用隔离 Redis 命名空间的短期记忆。"""
    from mediZJ.memory.short_term import ShortTermMemory

    return ShortTermMemory()


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
    config.addinivalue_line("markers", "infrastructure: 隔离基础设施验证")
    config.addinivalue_line(
        "markers", "integration: 需要外部服务 (LLM/Milvus/Redis/网络)"
    )
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
    skip_integration = pytest.mark.skip(
        reason="需要 --run-integration 标志才能运行集成测试"
    )
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_integration)


@pytest.fixture
async def mysql_infrastructure(monkeypatch):
    """仅清理显式指定的独立测试库，禁止对业务库执行清理。"""
    import os
    import uuid
    from sqlalchemy.engine import make_url
    from mediZJ.infrastructure.database import (
        close_database,
        initialize_database,
        transaction,
    )
    from mediZJ.infrastructure.redis_client import close_redis, initialize_redis
    from mediZJ.infrastructure.schema import metadata
    from mediZJ.infrastructure.settings import get_settings

    url = os.environ.get("TEST_MYSQL_URL")
    redis_url = os.environ.get("TEST_REDIS_URL")
    if not url or not redis_url:
        pytest.fail("基础设施测试需 TEST_MYSQL_URL 和 TEST_REDIS_URL")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("测试库名必须以 _test 结尾")
    monkeypatch.setenv("MYSQL_URL", url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv(
        "MILVUS_URI", os.environ.get("TEST_MILVUS_URI", "http://127.0.0.1:19530")
    )
    monkeypatch.setenv("APP_ENVIRONMENT", "test-" + uuid.uuid4().hex)
    get_settings.cache_clear()
    await initialize_database()
    await initialize_redis()
    try:
        async with transaction() as conn:
            for table in reversed(metadata.sorted_tables):
                await conn.execute(f"DELETE FROM `{table.name}`")
            for name in ("runs", "knowledge", "llm"):
                await conn.execute("INSERT INTO admission VALUES (%s)", (name,))
            settings = get_settings()
            for resource, count in (
                ("llm", settings.llm_max_concurrency),
                ("llm_wait", settings.llm_queue_limit),
            ):
                for index in range(count):
                    await conn.execute(
                        "INSERT INTO capacity_slots(slot_id,resource) VALUES (%s,%s)",
                        (f"{resource}:{index}", resource),
                    )
        yield
    finally:
        await close_redis()
        await close_database()
        get_settings.cache_clear()


async def _execute_sql(query, parameters=()):
    """测试直接通过真实 MySQL 事务准备数据与检查持久化结果。"""
    from mediZJ.infrastructure.database import transaction

    async with transaction() as conn:
        return await conn.execute(query, parameters)


@pytest.fixture
def execute_sql():
    return _execute_sql


@pytest.fixture(autouse=True)
def mock_unit_capacity(request, monkeypatch):
    """单元测试模拟集群容量边界，真实容量在基础设施测试中验证。"""
    if request.node.get_closest_marker("integration"):
        return
    from contextlib import asynccontextmanager
    from mediZJ.core import llm_client

    @asynccontextmanager
    async def capacity():
        yield

    monkeypatch.setattr(llm_client, "llm_capacity", capacity)
