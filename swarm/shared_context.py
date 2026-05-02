"""
SharedContext：Agent 群体智能的共享环境（信息素系统）

类比：
- 蚁群：蚂蚁通过信息素在地面留下痕迹，其他蚂蚁通过感知信息素来决定行动
- Swarm：Agent 通过 SharedContext 留下数据，其他 Agent 通过读取数据来决定行动

这是去中心化协作的核心：没有中心控制节点，只有共享环境
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional
import uuid
from collections import defaultdict

from .events import Event, EventType


class TaskStatus(Enum):
    """子任务状态"""
    PENDING = "pending"          # 等待认领
    CLAIMED = "claimed"          # 已认领
    IN_PROGRESS = "in_progress"  # 执行中
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"            # 失败


@dataclass
class SubTask:
    """
    子任务数据类

    LeadAgent 分解任务后发布到 SharedContext
    直接指定由哪个 Agent 执行
    """
    id: str
    type: str  # 任务类型：risk_assessment, diagnosis, research
    description: str
    assigned_agent: str  # 指定执行的 Agent ID（如 "consultation_agent"）
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None  # 执行结果
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他 SubTask ID

    def can_be_executed(self) -> bool:
        """判断是否可以被执行"""
        return self.status == TaskStatus.PENDING

    def start(self):
        """开始执行任务"""
        if not self.can_be_executed():
            raise ValueError(f"SubTask {self.id} cannot be started (status={self.status.value})")
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now()

    def complete(self, result: Dict[str, Any]):
        """完成任务"""
        self.result = result
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()

    def fail(self, error: str):
        """任务失败"""
        self.result = {"error": error}
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now()


@dataclass
class Contribution:
    """
    Agent 贡献数据类

    WorkerAgent 完成子任务后写入 SharedContext，
    供其他 Agent 读取和参考
    """
    agent_id: str
    subtask_id: str
    result: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    confidence: float = 1.0  # 置信度（0-1）
    metadata: Dict[str, Any] = field(default_factory=dict)


class SharedContext:
    """
    共享环境：Agent 之间的通信介质

    核心特性：
    1. 去中心化：没有中心控制节点
    2. 黑板系统：所有 Agent 都能读写
    3. 事件驱动：通过事件通知变化
    4. 时间有序：所有操作都有时间戳

    设计灵感：蚁群的信息素系统
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.created_at = datetime.now()

        # 共享数据存储
        self.data: Dict[str, Any] = {}

        # 事件流（按时间顺序）
        self.events: List[Event] = []

        # 任务分解（LeadAgent 发布）
        self.task_decomposition: Dict[str, SubTask] = {}

        # Agent 贡献（WorkerAgent 写入）
        self.agent_contributions: Dict[str, List[Contribution]] = defaultdict(list)

        # 工作记忆池（临时数据）
        self.memory_pool: Dict[str, Any] = {}

    def publish_event(self, event: Event):
        """
        发布事件

        Agent 通过发布事件来通知其他 Agent
        """
        self.events.append(event)

    def get_events(
        self,
        event_type: Optional[EventType] = None,
        source_agent: Optional[str] = None,
        target_agent: Optional[str] = None
    ) -> List[Event]:
        """
        获取事件列表

        可以根据事件类型、来源 Agent、目标 Agent 进行过滤
        """
        events = self.events

        if event_type:
            events = [e for e in events if e.type == event_type]

        if source_agent:
            events = [e for e in events if e.source_agent == source_agent]

        if target_agent:
            events = [e for e in events if e.is_for_agent(target_agent)]

        return events

    def add_subtask(self, subtask: SubTask):
        """添加子任务"""
        self.task_decomposition[subtask.id] = subtask

        # 发布事件
        self.publish_event(Event(
            type=EventType.TASK_DECOMPOSED,
            source_agent="lead_agent",
            data={
                "subtask_id": subtask.id,
                "type": subtask.type,
                "assigned_agent": subtask.assigned_agent
            }
        ))

    def get_subtask(self, subtask_id: str) -> Optional[SubTask]:
        """获取子任务"""
        return self.task_decomposition.get(subtask_id)

    def get_subtasks_for_agent(self, agent_id: str) -> List[SubTask]:
        """
        获取分配给指定 Agent 的待执行任务
        """
        tasks = []
        for subtask in self.task_decomposition.values():
            if subtask.assigned_agent == agent_id and subtask.can_be_executed():
                tasks.append(subtask)

        return tasks

    def start_subtask(self, subtask_id: str) -> bool:
        """
        开始执行子任务

        返回是否成功开始
        """
        subtask = self.get_subtask(subtask_id)
        if not subtask or not subtask.can_be_executed():
            return False

        try:
            subtask.start()

            # 发布事件
            self.publish_event(Event(
                type=EventType.SUBTASK_STARTED,
                source_agent=subtask.assigned_agent,
                data={"subtask_id": subtask_id}
            ))

            return True
        except ValueError:
            return False

    def complete_subtask(
        self,
        subtask_id: str,
        agent_id: str,
        result: Dict[str, Any],
        confidence: float = 1.0
    ):
        """完成子任务并添加贡献"""
        subtask = self.get_subtask(subtask_id)
        if not subtask:
            raise ValueError(f"SubTask {subtask_id} not found")

        if subtask.assigned_agent != agent_id:
            raise ValueError(f"SubTask {subtask_id} not assigned to {agent_id}")

        # 完成子任务
        subtask.complete(result)

        # 添加贡献
        contribution = Contribution(
            agent_id=agent_id,
            subtask_id=subtask_id,
            result=result,
            confidence=confidence
        )
        self.agent_contributions[agent_id].append(contribution)

        # 发布事件
        self.publish_event(Event(
            type=EventType.SUBTASK_COMPLETED,
            source_agent=agent_id,
            data={
                "subtask_id": subtask_id,
                "result_summary": str(result)[:200]  # 简短摘要
            }
        ))

    def get_contributions(
        self,
        agent_id: Optional[str] = None,
        subtask_id: Optional[str] = None
    ) -> List[Contribution]:
        """
        获取 Agent 贡献

        可以根据 Agent ID 或 SubTask ID 过滤
        """
        if agent_id:
            contributions = self.agent_contributions.get(agent_id, [])
        else:
            contributions = []
            for agent_contribs in self.agent_contributions.values():
                contributions.extend(agent_contribs)

        if subtask_id:
            contributions = [c for c in contributions if c.subtask_id == subtask_id]

        return contributions

    def get_all_completed_subtasks(self) -> List[SubTask]:
        """获取所有已完成的子任务"""
        return [
            subtask for subtask in self.task_decomposition.values()
            if subtask.status == TaskStatus.COMPLETED
        ]

    def is_all_subtasks_completed(self) -> bool:
        """判断是否所有子任务都已完成"""
        if not self.task_decomposition:
            return False

        return all(
            subtask.status == TaskStatus.COMPLETED
            for subtask in self.task_decomposition.values()
        )

    def set_data(self, key: str, value: Any):
        """设置共享数据"""
        self.data[key] = value

        # 发布事件
        self.publish_event(Event(
            type=EventType.CONTEXT_UPDATED,
            source_agent="system",
            data={"key": key}
        ))

    def get_data(self, key: str, default: Any = None) -> Any:
        """获取共享数据"""
        return self.data.get(key, default)

    def get_summary(self) -> Dict[str, Any]:
        """
        获取共享上下文摘要

        用于调试和日志记录
        """
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "total_events": len(self.events),
            "total_subtasks": len(self.task_decomposition),
            "completed_subtasks": len(self.get_all_completed_subtasks()),
            "agent_count": len(self.agent_contributions),
            "agents": list(self.agent_contributions.keys())
        }
