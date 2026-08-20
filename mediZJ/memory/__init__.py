"""
记忆系统：Agent 的持久化学习和记忆管理

包含：
- ShortTermMemory：会话级对话历史（内存/Redis）
- MedicalMemoryContextBuilder：统一分层记忆上下文
- MemoryEntropyManager：熵管理器（去重和压缩）
- SessionDB：SQLite 会话数据库
- SessionVectorStore：Milvus 会话向量索引
"""

# 短期和长期记忆
from .short_term import (
    ShortTermMemory,
    ConversationHistory
)
from .context_builder import MedicalMemoryContext, MedicalMemoryContextBuilder
from .prompt_prefix import PromptPrefixAssembler
from .structured_memory import StructuredMemoryStore

# Harness Engineering: 熵管理
from .entropy_manager import (
    MemoryEntropyManager
)

# 本地 Markdown 持久化
from .session_summary import (
    SessionSummary,
    SessionSummaryManager,
    AgentParticipation,
    KeyFinding,
    Lesson,
    PerformanceMetrics
)
from .personal_profile import (
    PersonalProfile,
    MedicalRecord,
    PendingItem,
)

# SQLite + Milvus 会话持久化
from .session_db import SessionDB
from .session_vector_store import SessionVectorStore

__all__ = [
    # 短期和长期记忆
    'ShortTermMemory',
    'ConversationHistory',
    'MedicalMemoryContext',
    'MedicalMemoryContextBuilder',
    'PromptPrefixAssembler',
    'StructuredMemoryStore',
    # Harness Engineering: 熵管理
    'MemoryEntropyManager',
    # 本地持久化类
    'SessionSummary',
    'SessionSummaryManager',
    'AgentParticipation',
    'KeyFinding',
    'Lesson',
    'PerformanceMetrics',
    'PersonalProfile',
    'MedicalRecord',
    'PendingItem',
    # SQLite + Milvus 会话持久化
    'SessionDB',
    'SessionVectorStore',
]
