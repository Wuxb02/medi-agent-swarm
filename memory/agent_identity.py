"""
AgentIdentity：Agent 身份管理

每个 Agent 都有一个 IDENTITY.md 文件，记录：
- 核心能力
- 专长领域
- 协作经验
- 工具使用统计

这是群体智能"持续进化"的关键机制
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import json


@dataclass
class CollaborationRecord:
    """协作记录"""
    partner_agent: str
    collaboration_count: int
    efficiency_improvement: float  # 效率提升百分比
    notes: str = ""


@dataclass
class ToolUsageStats:
    """工具使用统计"""
    tool_name: str
    usage_count: int
    success_rate: float
    avg_execution_time: float = 0.0


@dataclass
class AgentIdentity:
    """
    Agent 身份数据类

    记录 Agent 的能力和协作经验
    """
    agent_id: str
    agent_type: str  # consultation, diagnostic, research, etc.
    core_capabilities: List[str]
    expertise_domains: List[str]
    collaboration_records: List[CollaborationRecord] = field(default_factory=list)
    tool_usage_stats: List[ToolUsageStats] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)

    def update_collaboration(self, partner_agent: str, efficiency_improvement: float):
        """更新协作记录"""
        for record in self.collaboration_records:
            if record.partner_agent == partner_agent:
                record.collaboration_count += 1
                # 更新平均效率提升
                record.efficiency_improvement = (
                    record.efficiency_improvement * 0.7 + efficiency_improvement * 0.3
                )
                return

        # 新的协作伙伴
        self.collaboration_records.append(CollaborationRecord(
            partner_agent=partner_agent,
            collaboration_count=1,
            efficiency_improvement=efficiency_improvement
        ))
        self.last_updated = datetime.now()

    def update_tool_stats(self, tool_name: str, success: bool, execution_time: float):
        """更新工具使用统计"""
        for stats in self.tool_usage_stats:
            if stats.tool_name == tool_name:
                stats.usage_count += 1
                # 更新成功率（指数移动平均）
                stats.success_rate = (
                    stats.success_rate * 0.9 + (1.0 if success else 0.0) * 0.1
                )
                # 更新平均执行时间
                stats.avg_execution_time = (
                    stats.avg_execution_time * 0.8 + execution_time * 0.2
                )
                return

        # 新工具
        self.tool_usage_stats.append(ToolUsageStats(
            tool_name=tool_name,
            usage_count=1,
            success_rate=1.0 if success else 0.0,
            avg_execution_time=execution_time
        ))
        self.last_updated = datetime.now()

    def to_markdown(self) -> str:
        """转换为 IDENTITY.md 格式"""
        lines = [
            f"# Agent: {self.agent_id}",
            "",
            "## 核心能力",
            *[f"- {cap}" for cap in self.core_capabilities],
            "",
            "## 专长领域",
            *[f"- {domain}" for domain in self.expertise_domains],
            "",
            "## 协作经验",
            ""
        ]

        for collab in self.collaboration_records:
            lines.append(
                f"- 与 {collab.partner_agent} 协作 {collab.collaboration_count} 次，"
                f"效率提升 {collab.efficiency_improvement:.1%}"
            )
            if collab.notes:
                lines.append(f"  > {collab.notes}")

        lines.extend([
            "",
            "## 工具使用统计",
            ""
        ])

        for stats in self.tool_usage_stats:
            lines.append(
                f"- {stats.tool_name}: {stats.usage_count} 次"
                f"（成功率 {stats.success_rate:.1%}）"
            )

        lines.extend([
            "",
            f"**创建时间**: {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**最后更新**: {self.last_updated.strftime('%Y-%m-%d %H:%M:%S')}"
        ])

        return "\n".join(lines)

    @classmethod
    def from_markdown(cls, agent_id: str, markdown_content: str) -> "AgentIdentity":
        """从 Markdown 格式解析（简化实现）"""
        # 这里是简化版，实际项目中可以用更复杂的解析
        identity = cls(
            agent_id=agent_id,
            agent_type=agent_id.split("_")[0] if "_" in agent_id else agent_id,
            core_capabilities=[],
            expertise_domains=[]
        )
        return identity


class AgentIdentityManager:
    """
    Agent 身份管理器

    负责加载、保存和更新 Agent 的 IDENTITY.md 文件
    """

    def __init__(self, base_dir: str = "memory/agents"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_identity_path(self, agent_id: str) -> Path:
        """获取 Agent 的 IDENTITY.md 文件路径"""
        agent_dir = self.base_dir / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir / "IDENTITY.md"

    def load_identity(self, agent_id: str) -> Optional[AgentIdentity]:
        """加载 Agent 身份"""
        identity_path = self._get_identity_path(agent_id)

        if not identity_path.exists():
            return None

        try:
            content = identity_path.read_text(encoding="utf-8")
            return AgentIdentity.from_markdown(agent_id, content)
        except Exception as e:
            print(f"Error loading identity for {agent_id}: {e}")
            return None

    def save_identity(self, identity: AgentIdentity):
        """保存 Agent 身份"""
        identity_path = self._get_identity_path(identity.agent_id)

        try:
            content = identity.to_markdown()
            identity_path.write_text(content, encoding="utf-8")
        except Exception as e:
            print(f"Error saving identity for {identity.agent_id}: {e}")

    def create_identity(
        self,
        agent_id: str,
        agent_type: str,
        core_capabilities: List[str],
        expertise_domains: List[str]
    ) -> AgentIdentity:
        """创建新的 Agent 身份"""
        identity = AgentIdentity(
            agent_id=agent_id,
            agent_type=agent_type,
            core_capabilities=core_capabilities,
            expertise_domains=expertise_domains
        )

        self.save_identity(identity)
        return identity


    def update_collaboration(
        self,
        agent_id: str,
        partner_agent: str,
        efficiency_improvement: float
    ):
        """更新协作记录"""
        identity = self.load_identity(agent_id)
        if not identity:
            return

        identity.update_collaboration(partner_agent, efficiency_improvement)
        self.save_identity(identity)

    def update_tool_stats(
        self,
        agent_id: str,
        tool_name: str,
        success: bool,
        execution_time: float
    ):
        """更新工具使用统计"""
        identity = self.load_identity(agent_id)
        if not identity:
            return

        identity.update_tool_stats(tool_name, success, execution_time)
        self.save_identity(identity)
