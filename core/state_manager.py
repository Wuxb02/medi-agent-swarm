"""
状态管理器
管理Agent执行过程中的状态
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentState:
    """Agent状态"""
    task_id: str
    agent_id: str
    status: TaskStatus = TaskStatus.PENDING
    iteration: int = 0
    max_iterations: int = 5
    input_data: Dict[str, Any] = field(default_factory=dict)
    intermediate_results: List[Dict[str, Any]] = field(default_factory=list)
    final_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def is_completed(self) -> bool:
        """检查任务是否完成"""
        return self.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]

    def should_continue(self) -> bool:
        """检查是否应该继续迭代"""
        return (
            self.status == TaskStatus.IN_PROGRESS
            and self.iteration < self.max_iterations
            and not self.is_completed()
        )

    def add_intermediate_result(self, result: Dict[str, Any]):
        """添加中间结果"""
        self.intermediate_results.append({
            'iteration': self.iteration,
            'timestamp': datetime.now(),
            'result': result
        })
        self.updated_at = datetime.now()

    def mark_completed(self, result: Dict[str, Any]):
        """标记为完成"""
        self.status = TaskStatus.COMPLETED
        self.final_result = result
        self.updated_at = datetime.now()

    def mark_failed(self, error: str):
        """标记为失败"""
        self.status = TaskStatus.FAILED
        self.error = error
        self.updated_at = datetime.now()


class StateManager:
    """状态管理器，管理多个Agent的状态"""

    def __init__(self):
        self.states: Dict[str, AgentState] = {}

    def create_state(
        self,
        task_id: str,
        agent_id: str,
        input_data: Dict[str, Any],
        max_iterations: int = 5
    ) -> AgentState:
        """
        创建新的状态

        Args:
            task_id: 任务ID
            agent_id: Agent ID
            input_data: 输入数据
            max_iterations: 最大迭代次数

        Returns:
            Agent状态对象
        """
        state = AgentState(
            task_id=task_id,
            agent_id=agent_id,
            input_data=input_data,
            max_iterations=max_iterations,
            status=TaskStatus.PENDING
        )
        self.states[task_id] = state
        return state

    def get_state(self, task_id: str) -> Optional[AgentState]:
        """获取状态"""
        return self.states.get(task_id)

    def update_state(self, task_id: str, **kwargs):
        """更新状态"""
        if task_id in self.states:
            state = self.states[task_id]
            for key, value in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            state.updated_at = datetime.now()

    def delete_state(self, task_id: str):
        """删除状态"""
        if task_id in self.states:
            del self.states[task_id]

    def get_active_tasks(self) -> List[AgentState]:
        """获取所有活跃的任务"""
        return [
            state for state in self.states.values()
            if state.status == TaskStatus.IN_PROGRESS
        ]

    def cleanup_old_states(self, hours: int = 24):
        """清理旧状态"""
        cutoff_time = datetime.now().timestamp() - hours * 3600
        to_delete = [
            task_id for task_id, state in self.states.items()
            if state.updated_at.timestamp() < cutoff_time
        ]
        for task_id in to_delete:
            del self.states[task_id]
