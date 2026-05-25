import api from './client'

export interface TraceSummary {
  trace_id: string
  session_id: string
  status: string
  start_time: string
  duration_ms: number | null
  mode: string
  total_tokens: number
  agents_involved: string[]
  span_count: number
  question_summary: string
}

export interface TraceListResponse {
  traces: TraceSummary[]
  total: number
  limit: number
  offset: number
}

export interface WaterfallSpan {
  id: string
  parent_id: string | null
  span_type: string
  name: string
  status: string
  start_offset_ms: number
  duration_ms: number
  depth: number
  error_message?: string | null
  attributes: Record<string, any>
}

export interface WaterfallResponse {
  trace_id: string
  total_duration_ms: number
  spans: WaterfallSpan[]
}

export interface AgentStats {
  [agentId: string]: {
    call_count: number
    avg_duration_ms: number
    p50_ms: number
    p90_ms: number
    success_rate: number
    avg_tokens: number
  }
}

export interface ToolStats {
  [toolName: string]: {
    call_count: number
    avg_duration_ms: number
    success_rate: number
  }
}

export interface LLMStats {
  call_count: number
  avg_latency_ms: number
  p50_ms: number
  p90_ms: number
  avg_prompt_tokens: number
  avg_completion_tokens: number
  total_prompt_tokens: number
  total_completion_tokens: number
}

export interface StageBreakdown {
  [stageName: string]: number
}

export interface SlowTraceItem {
  trace_id: string
  session_id: string
  duration_ms: number
  mode: string
  agents_involved: string[]
  question_summary: string
}

export interface ErrorTraceItem {
  trace_id: string
  session_id: string
  duration_ms: number
  mode: string
  question_summary: string
  start_time: string
}

export async function getTraces(limit = 50, offset = 0, sessionId?: string) {
  const { data } = await api.get<TraceListResponse>('/traces', {
    params: { limit, offset, session_id: sessionId },
  })
  return data
}

export async function getTraceTree(traceId: string) {
  const { data } = await api.get(`/traces/${traceId}`)
  return data
}

export async function getTraceSpans(traceId: string) {
  const { data } = await api.get(`/traces/${traceId}/spans`)
  return data.spans || []
}

export async function getTraceWaterfall(traceId: string) {
  const { data } = await api.get<WaterfallResponse>(`/traces/${traceId}/waterfall`)
  return data
}

export async function getTraceStages(traceId: string) {
  const { data } = await api.get(`/traces/${traceId}/stages`)
  return data.stages || {}
}

export async function getAgentStats(days = 7) {
  const { data } = await api.get('/traces/stats/agents', { params: { days } })
  return data.stats || {}
}

export async function getToolStats(days = 7) {
  const { data } = await api.get('/traces/stats/tools', { params: { days } })
  return data.stats || {}
}

export async function getLLMStats(days = 7) {
  const { data } = await api.get('/traces/stats/llm', { params: { days } })
  return data.stats || {}
}

export async function getSlowTraces(thresholdMs = 30000, limit = 10) {
  const { data } = await api.get('/traces/stats/slow', {
    params: { threshold_ms: thresholdMs, limit },
  })
  return data.traces || []
}

export async function getErrorTraces(days = 7, limit = 20) {
  const { data } = await api.get('/traces/stats/errors', {
    params: { days, limit },
  })
  return data.traces || []
}