"""
AgentSubGraph 状态定义

替代 AgentLoop.run() 的 while 循环状态，每个 Worker Agent 独立运行一个 AgentSubGraph。
"""
from typing import TypedDict, Annotated, Optional, List, Dict, Any
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """Agent 子图状态（替代 AgentLoop 的实例变量）

    每个字段对应 AgentLoop 中的关键状态变量。
    """

    # === Agent 身份 ===
    agent_id: str
    sub_session_id: str
    session_id: str

    # === 子任务信息（Swarm 模式，单 Agent 模式这些为空） ===
    subtask_id: str
    subtask_type: str
    subtask_description: str

    # === 对话消息（OpenAI 格式） ===
    # 使用 LangGraph 的 add_messages reducer 自动追加
    messages: Annotated[List[Dict[str, Any]], add_messages]

    # === 循环控制（替代 AgentLoop 的 while state.should_continue()） ===
    iteration: int
    max_iterations: int            # 默认 10
    tool_call_count: int            # 已执行的非 activate_skill 工具调用次数
    max_tool_calls: int             # 默认 2
    force_answer: bool              # 达到上限，强制 LLM 生成最终答案

    # === Skill 状态（替代 SkillRegistry.active_skill） ===
    active_skill: Optional[str]     # 当前激活的 Skill 名称，None 表示未激活
    compat_mode: bool               # 兼容模式（平铺所有工具）

    # === 问卷控制（Human-in-the-Loop） ===
    questionnaire_pending: Optional[Dict[str, Any]]   # 待处理的问卷 {id, data}
    questionnaire_answers: Optional[Dict[str, Any]]   # 用户回答

    # === 结果收集 ===
    final_answer: str               # 最终回答文本
    references: List[Dict]          # 知识库引用列表（doc_id 去重）
    usage: Dict[str, int]           # token 用量 {prompt_tokens, completion_tokens, total_tokens}
    message_count: int              # 消息计数
    iterations: int                 # 实际迭代次数
    completed: bool                 # 是否完成
    error: Optional[str]            # 错误信息
