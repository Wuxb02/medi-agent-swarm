/** 前端类型定义 */

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
  metadata?: {
    swarmEnabled: boolean
    agentsInvolved: string[]
    totalTime?: number
    subtasksCompleted?: number
    timeoutOccurred?: boolean
    usage?: {
      prompt_tokens: number
      completion_tokens: number
      total_tokens: number
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
  data?: Record<string, any>
}

export interface ToolStep {
  toolName: string
  arguments: Record<string, any>
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

export interface SSEEvent {
  type: string
  data: Record<string, any>
}

export interface KnowledgeItem {
  id: string
  content: string
  metadata: Record<string, any>
  score: number
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
    agent_events?: any[]
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
