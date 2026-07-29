import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getTraces,
  getTraceWaterfall,
  getAgentStats,
  getToolStats,
  getLLMStats,
  getSlowTraces,
} from '../api/trace'
import type {
  TraceListResponse,
  WaterfallResponse,
  AgentStats,
  ToolStats,
  LLMStats,
  SlowTraceItem,
} from '../api/trace'

export const useTraceStore = defineStore('trace', () => {
  const traceList = ref<TraceListResponse | null>(null)
  const waterfall = ref<WaterfallResponse | null>(null)
  const agentStats = ref<AgentStats | null>(null)
  const toolStats = ref<ToolStats | null>(null)
  const llmStats = ref<LLMStats | null>(null)
  const slowTraces = ref<SlowTraceItem[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchTraces(limit = 50, offset = 0, sessionId?: string) {
    loading.value = true
    error.value = null
    try {
      traceList.value = await getTraces(limit, offset, sessionId)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    } finally {
      loading.value = false
    }
  }

  async function fetchWaterfall(traceId: string) {
    try {
      waterfall.value = await getTraceWaterfall(traceId)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  async function fetchStats(days = 7) {
    try {
      const [agents, tools, llm, slow] = await Promise.all([
        getAgentStats(days),
        getToolStats(days),
        getLLMStats(days),
        getSlowTraces(),
      ])
      agentStats.value = agents
      toolStats.value = tools
      llmStats.value = llm
      slowTraces.value = slow
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
    }
  }

  return {
    traceList,
    waterfall,
    agentStats,
    toolStats,
    llmStats,
    slowTraces,
    loading,
    error,
    fetchTraces,
    fetchWaterfall,
    fetchStats,
  }
})
