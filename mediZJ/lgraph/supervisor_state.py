"""
SupervisorGraph 状态定义

替代 SwarmCoordinator.process() 流水线中的状态管理。
使用 Annotated + operator 实现 Map-Reduce 状态聚合。
"""
import operator
from typing import TypedDict, Annotated, Optional, List, Dict, Any
from langgraph.graph.message import add_messages


class SupervisorState(TypedDict, total=False):
    """主图状态（替代 SwarmCoordinator 的实例变量 + SharedContext）

    每个字段对应 process() 流水线中的关键数据。
    """

    # === 输入 ===
    question: str
    session_id: str
    context: Dict[str, Any]
    start_time: str                 # ISO timestamp

    # === 消息（用于 Human-in-the-Loop clarify 阶段） ===
    messages: Annotated[List[Dict[str, Any]], add_messages]

    # === 记忆检索结果 ===
    recent_history: List[Dict[str, Any]]
    similar_memories: List[Dict[str, Any]]
    personal_profile: str
    collected_info: str             # clarify 阶段收集的信息

    # === 意图识别（检索门控） ===
    intent: str                     # medical | others
    intent_confidence: float        # 0.0 ~ 1.0
    intent_source: str              # "llm" | "fallback"
    skip_long_term_retrieval: bool  # 是否跳过 Mem0 长期记忆检索
    chat_mode: bool                 # others 意图时直接聊天回应，跳过任务分解

    # === 任务分解 ===
    subtasks: List[Dict[str, Any]]  # LeadAgent JSON 格式
    route_decision: str             # "single" | "swarm" | "fallback"

    # === Swarm Map-Reduce 数据（替代 SharedContext） ===
    # 使用 operator.ior 确保并行 Worker 的结果累加而非覆盖
    swarm_contributions: Annotated[Dict[str, List[Dict]], operator.ior]
    # 事件日志
    swarm_events: Annotated[List[Dict], operator.add]
    # subtask 状态追踪: subtask_id -> status (pending/in_progress/completed)
    swarm_subtasks_status: Annotated[Dict[str, str], operator.ior]

    # === Agent 执行结果 ===
    agent_results: Dict[str, Dict[str, Any]]    # agent_id -> LoopResult
    single_agent_answer: str

    # === 引用系统 ===
    all_references: Dict[str, Dict]             # doc_id -> ref（跨 Worker 去重）
    renumber_map: Dict[str, Dict[int, int]]     # agent_id -> {old_index: new_index}

    # === 综合与最终输出 ===
    final_answer: str
    citations: List[Dict]
    suggestions: List[str]
    usage: Dict[str, int]
    agents_involved: List[str]
    swarm_enabled: bool
    mode: str                       # "single_agent" | "swarm" | "fallback"
    route_reason: str
    total_time: float
    timeout_occurred: Annotated[bool, operator.or_]
    swarm_metadata: Dict[str, Any]
    performance_metrics: Dict[str, Any]

    # === Human-in-the-Loop 控制 ===
    clarify_round: int              # 当前澄清轮次（0 起，最多 3 轮）
    clarify_complete: bool
    clarify_answers: Dict[str, Any]
    clarify_timeout_skipped: bool
    # 各轮澄清记录 {round, payload, answers}（跨轮累积）
    clarify_rounds: Annotated[List[Dict[str, Any]], operator.add]
    # 待挂起的问卷 payload（clarify_ask 节点的 interrupt 载荷）
    clarify_pending: Optional[Dict[str, Any]]

    # === 内部追踪 ===
    _swarm_finalized: bool           # Swarm 路径标记，避免 _finalize 重复处理
