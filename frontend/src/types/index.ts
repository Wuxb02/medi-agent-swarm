/** 前端类型定义 */

// ============================================================
// SSE 事件类型系统
// ============================================================

/** 所有 SSE 事件类型 */
export type SSEEventType =
  | 'start'
  | 'task_decomposed'
  | 'agent_start'
  | 'agent_tool_call'
  | 'agent_tool_result'
  | 'agent_complete'
  | 'agent_thinking'
  | 'agent_tool_step'
  | 'agent_thinking_done'
  | 'agent_content_delta'
  | 'agent_questionnaire'
  | 'agent_questionnaire_cancelled'
  | 'trace_span'
  | 'suggestions'
  | 'done'
  | 'error'

/** SSE 原始消息结构 */
export interface SSEMessage {
  event: string
  data: Record<string, unknown>
}

// ---- 各事件的 data 类型 ----

export interface StartData {
  session_id: string
}

export interface TaskDecomposedData {
  id?: string
  timestamp?: string
  data?: {
    subtask_id?: string
    type?: string
    description?: string
    assigned_agent?: string
  }
}

export interface AgentStartData {
  source_agent?: string
  agent_id?: string
  id?: string
  timestamp?: string
  data?: {
    subtask_id?: string
    subtask_type?: string
    tool_calls?: number
    mode?: string
    [key: string]: unknown
  }
}

export interface AgentCompleteData {
  source_agent?: string
  agent_id?: string
  id?: string
  timestamp?: string
  data?: {
    execution_time?: number
    subtasks_completed?: number
    [key: string]: unknown
  }
}

export interface AgentThinkingData {
  source_agent?: string
  id?: string
  timestamp?: string
  data?: {
    content: string
    iteration: number
    [key: string]: unknown
  }
}

export interface AgentToolStepData {
  source_agent?: string
  id?: string
  timestamp?: string
  data?: {
    tool_name: string
    arguments: Record<string, unknown>
    result: unknown
    success?: boolean
    iteration: number
  }
}

export interface AgentThinkingDoneData {
  source_agent?: string
  id?: string
  timestamp?: string
  data?: {
    iteration: number
    elapsed_seconds?: number
  }
}

export interface AgentContentDeltaData {
  data?: {
    token: string
  }
}

export interface AgentQuestionnaireData {
  questionnaire_id: string
  questionnaire_data?: {
    questions: QuestionnaireQuestion[]
  }
  data?: {
    questionnaire_id: string
    questionnaire_data?: {
      questions: QuestionnaireQuestion[]
    }
  }
}

export interface DoneData {
  answer: string
  disclaimer?: string
  citations?: Citation[]
  swarm_enabled?: boolean
  agents_involved?: string[]
  total_time?: number
  usage?: {
    prompt_tokens?: number
    completion_tokens?: number
    total_tokens?: number
  }
  performance_metrics?: {
    parallel_efficiency?: number
    information_coverage?: number
    redundancy?: number
  }
}

export interface ErrorData {
  error: string
}

// ============================================================
// UI 模型
// ============================================================

export interface QuestionOption {
  label: string
  description?: string
}

export interface QuestionnaireQuestion {
  header: string
  type: 'enum' | 'multi' | 'input'
  required: boolean
  text: string
  options: QuestionOption[]
}

export interface QuestionnaireData {
  questionnaire_id: string
  questions: QuestionnaireQuestion[]
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  isStreaming?: boolean
  suggestions?: string[]
  disclaimer?: string
  agentEvents?: AgentEvent[]
  thinkingBlocks?: ThinkingBlock[]
  delegations?: TaskDelegation[]
  questionnaire?: QuestionnaireData
  citations?: Citation[]
  metadata?: {
    swarmEnabled: boolean
    agentsInvolved: string[]
    totalTime?: number
    subtasksCompleted?: number
    timeoutOccurred?: boolean
    usage?: {
      prompt_tokens?: number
      completion_tokens?: number
      total_tokens?: number
    }
    performanceMetrics?: {
      parallelEfficiency: number
      informationCoverage: number
      redundancy: number
    }
  }
}

export interface AgentEvent {
  id: string
  type: 'decomposed' | 'start' | 'tool_call' | 'tool_result' | 'complete'
  agentId: string
  subtaskId?: string
  subtaskType?: string
  toolName?: string
  timestamp: string
  data?: Record<string, unknown>
}

export interface TaskDelegation {
  subtaskId: string
  type: string
  description: string
  assignedAgent: string
}

export interface ToolStep {
  toolName: string
  arguments: Record<string, unknown>
  result: string
  success: boolean
}

export interface ThinkingBlock {
  id: string
  agentId: string
  thinking: string
  iteration: number
  toolSteps: ToolStep[]
  elapsedSeconds?: number
  isCollapsed: boolean
}

export interface KnowledgeItem {
  id: string
  content: string
  metadata: Record<string, string>
  score: number
}

export interface Citation {
  index: number
  doc_id: string
  source: string
  disease: string
  type: string
  filename: string
  score: number
  snippet: string
  content: string
}

export interface DocumentSummary {
  doc_id: string
  filename: string
  type: string
  disease: string
  source: string
  chunk_count: number
}

export interface ChunkDetail {
  milvus_id: number
  chunk_id: number
  content: string
  total_chunks: number
}

export interface SessionItem {
  session_id: string
  first_question: string
  created_at: string
  message_count: number
  mode: string
  total_tokens: number
  parallel_efficiency: number
  information_coverage: number
  redundancy: number
  _isNew?: boolean
}

export interface SessionTurn {
  turn_index: number
  user_message: {
    role: string
    content: string
    timestamp?: string
  }
  assistant_message: {
    role: string
    content: string
    timestamp?: string
    agent_events?: unknown[]
    suggestions?: string[]
    disclaimer?: string
    mode?: string
    agents_involved?: string[]
    total_time?: number
    total_tokens?: number
    subtasks_completed?: number
  }
}

export interface DashboardStats {
  total_sessions: number
  total_messages: number
  swarm_sessions: number
  single_sessions: number
  avg_response_time: number
  agents_usage: Record<string, number>
  knowledge_base_size: number
  recent_sessions: SessionItem[]
  total_tokens: number
  avg_parallel_efficiency: number
  avg_information_coverage: number
  avg_redundancy: number
}
