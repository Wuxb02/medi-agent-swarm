"""真实模型集成测试使用独立基础设施与应用资源。"""

import asyncio

import pytest_asyncio
from dotenv import load_dotenv

from mediZJ.infrastructure.database import transaction
from mediZJ.knowledge.milvus_kb import MedicalKnowledgeBase
from mediZJ.memory.session_vector_store import SessionVectorStore


@pytest_asyncio.fixture(autouse=True)
async def application_resources(mysql_infrastructure):
    """初始化默认测试用户及向量集合，连接由共享测试 fixture 管理。"""
    load_dotenv(".env")
    async with transaction() as conn:
        await conn.execute(
            "INSERT INTO users (user_id,username,username_normalized,role,"
            "is_active,created_at) VALUES ('default','default','default','user',"
            "1,DATE_FORMAT(UTC_TIMESTAMP(),'%%Y-%%m-%%dT%%H:%%i:%%s+00:00'))"
        )
    await asyncio.to_thread(MedicalKnowledgeBase, initialize=True)
    await asyncio.to_thread(SessionVectorStore, initialize=True)
    yield
