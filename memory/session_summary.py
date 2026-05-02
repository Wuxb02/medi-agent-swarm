"""
SessionSummary：会话总结和经验提取

每次 Swarm 协作后自动生成会话总结，记录：
- 问题和背景
- 参与的 Agent
- 协作过程
- 关键发现
- 经验教训
- 性能指标

这是群体智能"持续学习"的关键机制
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import json
from loguru import logger


@dataclass
class AgentParticipation:
    """Agent 参与记录"""
    agent_id: str
    role: str  # lead/worker
    subtasks_handled: List[str]
    tool_calls: int
    execution_time: float  # 秒
    contribution_quality: float = 1.0  # 0-1


@dataclass
class KeyFinding:
    """关键发现"""
    category: str  # diagnosis/risk/evidence/treatment
    finding: str
    source_agent: str
    confidence: float = 1.0


@dataclass
class Lesson:
    """经验教训"""
    agent_id: str
    lesson_type: str  # success/failure/improvement
    description: str
    actionable: str  # 可执行的改进措施


@dataclass
class PerformanceMetrics:
    """性能指标"""
    total_time: float  # 总耗时（秒）
    agent_count: int  # 参与 Agent 数量
    parallel_efficiency: float  # 并行效率（0-1）
    information_coverage: float  # 信息覆盖度（0-1）
    redundancy: float  # 信息冗余度（0-1）
    speedup_vs_single: float = 1.0  # 相比单 Agent 的加速比


@dataclass
class SessionSummary:
    """
    会话总结数据类

    记录一次完整的 Swarm 协作过程
    """
    session_id: str
    question: str
    context: Dict[str, Any]
    timestamp: datetime

    # 参与者
    agents_participated: List[AgentParticipation]

    # 过程
    subtasks_created: int
    subtasks_completed: int
    events_count: int

    # 结果
    final_answer: str
    key_findings: List[KeyFinding]

    # 学习
    lessons_learned: List[Lesson]

    # 性能
    performance: PerformanceMetrics

    # 元数据
    swarm_enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """转换为 Markdown 格式"""
        date_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"# Session Summary: {self.session_id}",
            "",
            f"**时间**: {date_str}",
            "",
            "## 问题",
            self.question,
            ""
        ]

        if self.context:
            lines.extend([
                "## 背景",
                "```json",
                json.dumps(self.context, ensure_ascii=False, indent=2),
                "```",
                ""
            ])

        lines.extend([
            "## 参与 Agent",
            ""
        ])

        for agent in self.agents_participated:
            lines.append(f"### {agent.agent_id} ({agent.role})")
            lines.append(f"- 处理子任务：{len(agent.subtasks_handled)} 个")
            lines.append(f"- 工具调用：{agent.tool_calls} 次")
            lines.append(f"- 执行时间：{agent.execution_time:.2f} 秒")
            lines.append("")

        lines.extend([
            "## 协作过程",
            "",
            f"- 创建子任务：{self.subtasks_created} 个",
            f"- 完成子任务：{self.subtasks_completed} 个",
            f"- 发布事件：{self.events_count} 个",
            ""
        ])

        if self.key_findings:
            lines.extend([
                "## 关键发现",
                ""
            ])

            for finding in self.key_findings:
                lines.append(f"### {finding.category.upper()}")
                lines.append(f"**来源**: {finding.source_agent}")
                lines.append(f"**发现**: {finding.finding}")
                lines.append(f"**置信度**: {finding.confidence:.1%}")
                lines.append("")

        lines.extend([
            "## 最终答案",
            "",
            self.final_answer[:500] + ("..." if len(self.final_answer) > 500 else ""),
            ""
        ])

        if self.lessons_learned:
            lines.extend([
                "## 经验教训",
                ""
            ])

            for lesson in self.lessons_learned:
                emoji = "✅" if lesson.lesson_type == "success" else "⚠️" if lesson.lesson_type == "failure" else "💡"
                lines.append(f"### {emoji} {lesson.agent_id}")
                lines.append(f"**{lesson.lesson_type.upper()}**: {lesson.description}")
                if lesson.actionable:
                    lines.append(f"**改进措施**: {lesson.actionable}")
                lines.append("")

        lines.extend([
            "## 性能指标",
            "",
            f"- 总耗时：{self.performance.total_time:.2f} 秒",
            f"- 参与 Agent：{self.performance.agent_count} 个",
            f"- 并行效率：{self.performance.parallel_efficiency:.1%}",
            f"- 信息覆盖度：{self.performance.information_coverage:.1%}",
            f"- 信息冗余度：{self.performance.redundancy:.1%}",
            f"- 加速比：{self.performance.speedup_vs_single:.2f}x",
            ""
        ])

        return "\n".join(lines)

    @classmethod
    def from_shared_context(
        cls,
        session_id: str,
        question: str,
        shared_context: Any,
        final_answer: str,
        start_time: datetime,
        end_time: datetime
    ) -> "SessionSummary":
        """从 SharedContext 构建 SessionSummary"""

        # 计算性能指标
        total_time = (end_time - start_time).total_seconds()

        # 提取 Agent 参与信息
        agents_participated = []
        for agent_id, contributions in shared_context.agent_contributions.items():
            tool_calls = sum(
                1 for c in contributions
                if c.result.get('success', True)
            )
            agents_participated.append(AgentParticipation(
                agent_id=agent_id,
                role="worker",
                subtasks_handled=[c.subtask_id for c in contributions],
                tool_calls=tool_calls,
                execution_time=total_time / len(shared_context.agent_contributions)
            ))

        # 提取关键发现
        key_findings = []
        for contrib in shared_context.get_contributions():
            if "risk_level" in contrib.result:
                key_findings.append(KeyFinding(
                    category="risk",
                    finding=f"风险等级：{contrib.result['risk_level']}",
                    source_agent=contrib.agent_id,
                    confidence=contrib.confidence
                ))

        # 性能指标
        performance = PerformanceMetrics(
            total_time=total_time,
            agent_count=len(shared_context.agent_contributions),
            parallel_efficiency=0.8,  # TODO: 实际计算
            information_coverage=0.9,  # TODO: 实际计算
            redundancy=0.15  # TODO: 实际计算
        )

        return cls(
            session_id=session_id,
            question=question,
            context={},
            timestamp=start_time,
            agents_participated=agents_participated,
            subtasks_created=len(shared_context.task_decomposition),
            subtasks_completed=len(shared_context.get_all_completed_subtasks()),
            events_count=len(shared_context.events),
            final_answer=final_answer,
            key_findings=key_findings,
            lessons_learned=[],  # TODO: 从协作过程中提取
            performance=performance
        )


class SessionSummaryManager:
    """
    会话总结管理器

    负责保存和检索会话总结
    """

    def __init__(self, base_dir: str = "memory/swarm/session_summaries"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_summary_path(self, session_id: str) -> Path:
        """获取会话总结文件路径"""
        # 按日期组织
        date_str = session_id.split("-")[0] if "-" in session_id else "unknown"
        date_dir = self.base_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir / f"{session_id}.md"

    def save_summary(self, summary: SessionSummary):
        """保存会话总结"""
        summary_path = self._get_summary_path(summary.session_id)

        try:
            content = summary.to_markdown()
            summary_path.write_text(content, encoding="utf-8")
            logger.info(f"Saved session summary: {summary.session_id}")
        except Exception as e:
            logger.error(f"Error saving session summary: {e}")

    def load_summary(self, session_id: str) -> Optional[SessionSummary]:
        """加载会话总结（简化实现）"""
        summary_path = self._get_summary_path(session_id)

        if not summary_path.exists():
            return None

        try:
            # 这里可以实现从 Markdown 解析回 SessionSummary
            # 简化版直接返回 None
            return None
        except Exception as e:
            logger.error(f"Error loading session summary: {e}")
            return None

    def search_similar_sessions(
        self,
        query: str,
        limit: int = 5
    ) -> List[Path]:
        """
        搜索相似的会话（简化实现）

        未来可以使用向量相似度搜索
        """
        # 简单实现：返回最近的会话
        all_summaries = list(self.base_dir.rglob("*.md"))
        all_summaries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return all_summaries[:limit]
