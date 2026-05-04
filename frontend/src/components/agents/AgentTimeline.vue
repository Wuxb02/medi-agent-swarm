<script setup lang="ts">
import { computed } from 'vue'
import type { AgentEvent } from '../../types'

const props = defineProps<{
  events: AgentEvent[]
  metadata?: {
    swarmEnabled: boolean
    agentsInvolved: string[]
    totalTime?: number
  }
}>()

interface TimelineAgent {
  id: string
  name: string
  status: 'pending' | 'running' | 'completed'
  subtaskType?: string
  duration?: string
}

const agentNameMap: Record<string, string> = {
  swarm_coordinator: '汇总输出',
  lead_agent: 'LeadAgent',
  consultation_agent: '健康咨询 Agent',
  diagnostic_agent: '症状诊断 Agent',
  research_agent: '医学研究 Agent',
}

const agents = computed(() => {
  const agentMap = new Map<string, TimelineAgent>()

  for (const evt of props.events) {
    if (evt.type === 'decomposed') continue

    const id = evt.agentId
    if (!agentMap.has(id)) {
      agentMap.set(id, {
        id,
        name: agentNameMap[id] || id,
        status: 'pending',
      })
    }

    const agent = agentMap.get(id)!
    if (evt.type === 'start') {
      agent.status = 'running'
      agent.subtaskType = evt.subtaskType
    } else if (evt.type === 'complete') {
      agent.status = 'completed'
    }
  }

  return Array.from(agentMap.values())
})

const statusColor: Record<string, string> = {
  pending: 'bg-slate-300',
  running: 'bg-blue-500 animate-pulse',
  completed: 'bg-green-500',
}
</script>

<template>
  <div v-if="agents.length > 0" class="bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs">
    <div class="flex items-center gap-2 mb-2 text-slate-500">
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
      <span>{{ metadata?.swarmEnabled ? 'Swarm 多 Agent 协作' : '单 Agent 处理' }}</span>
      <span v-if="metadata?.totalTime" class="ml-auto text-slate-400">{{ metadata.totalTime.toFixed(1) }}s</span>
    </div>
    <div class="flex flex-wrap gap-2">
      <div
        v-for="agent in agents"
        :key="agent.id"
        class="flex items-center gap-1.5 px-2 py-1 bg-white border border-slate-200 rounded"
      >
        <span class="w-2 h-2 rounded-full" :class="statusColor[agent.status]" />
        <span class="text-slate-600 font-medium">{{ agent.name }}</span>
        <span v-if="agent.subtaskType" class="text-slate-400">({{ agent.subtaskType }})</span>
        <span v-if="agent.status === 'completed'" class="text-green-500">✓</span>
      </div>
    </div>
  </div>
</template>
