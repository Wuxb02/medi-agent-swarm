import api from './client'

export interface ChatRequest {
  question: string
  session_id?: string
  context?: Record<string, any>
}

export interface ChatResponse {
  answer: string
  suggestions: string[]
  session_id: string
  swarm_enabled: boolean
  agents_involved: string[]
  subtasks_completed: number
  total_time: number
  swarm_metadata: Record<string, any>
  timeout_occurred: boolean
}

export async function sendChat(request: ChatRequest): Promise<ChatResponse> {
  const { data } = await api.post<ChatResponse>('/chat', request)
  return data
}

export async function getChatHistory(sessionId: string) {
  const { data } = await api.get(`/chat/history/${sessionId}`)
  return data
}
