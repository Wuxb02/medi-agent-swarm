<template>
  <div class="space-y-6">
    <!-- 统计卡 -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4">
        <div class="text-2xl font-bold text-blue-600">{{ llmStats.call_count }}</div>
        <div class="text-xs text-slate-500">LLM 调用次数</div>
        <div class="text-[11px] text-slate-400 mt-1">
          平均 {{ llmStats.avg_latency_ms?.toFixed(0) || 0 }}ms · P90
          {{ llmStats.p90_ms?.toFixed(0) || 0 }}ms
        </div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4">
        <div class="text-2xl font-bold text-green-600">{{ Object.keys(agentStats).length }}</div>
        <div class="text-xs text-slate-500">Agent 类型</div>
        <div class="text-[11px] text-slate-400 mt-1">成功率 {{ overallAgentSuccess }}%</div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4">
        <div class="text-2xl font-bold text-purple-600">{{ Object.keys(toolStats).length }}</div>
        <div class="text-xs text-slate-500">工具类型</div>
        <div class="text-[11px] text-slate-400 mt-1">调用 {{ totalToolCalls }} 次</div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4">
        <div class="text-2xl font-bold text-amber-600">{{ slowTraces.length }}</div>
        <div class="text-xs text-slate-500">慢 Trace (&gt;30s)</div>
        <div class="text-[11px] text-slate-400 mt-1">近 {{ slowDays }} 天</div>
      </div>
    </div>

    <!-- Agent 统计表 -->
    <div class="bg-white border border-slate-200 rounded-xl overflow-hidden">
      <div class="px-4 py-3 bg-slate-50 border-b border-slate-200">
        <span class="text-sm font-semibold text-slate-700">Agent 统计</span>
        <span class="text-xs text-slate-400 ml-2">近 {{ agentDays }} 天</span>
      </div>
      <table class="w-full text-sm" v-if="Object.keys(agentStats).length">
        <thead>
          <tr class="border-b border-slate-100 text-xs text-slate-500">
            <th class="text-left px-4 py-2">Agent</th>
            <th class="text-right px-4 py-2">调用次数</th>
            <th class="text-right px-4 py-2">Avg</th>
            <th class="text-right px-4 py-2">P50</th>
            <th class="text-right px-4 py-2">P90</th>
            <th class="text-right px-4 py-2">成功率</th>
            <th class="text-right px-4 py-2">Avg Tokens</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(stat, id) in agentStats"
            :key="id"
            class="border-b border-slate-50 hover:bg-slate-50"
          >
            <td class="px-4 py-2 text-slate-700 font-medium">{{ id }}</td>
            <td class="px-4 py-2 text-right text-slate-600">{{ stat.call_count }}</td>
            <td class="px-4 py-2 text-right text-slate-600">
              {{ stat.avg_duration_ms?.toFixed(0) }}ms
            </td>
            <td class="px-4 py-2 text-right text-slate-600">{{ stat.p50_ms?.toFixed(0) }}ms</td>
            <td class="px-4 py-2 text-right text-slate-600">{{ stat.p90_ms?.toFixed(0) }}ms</td>
            <td
              class="px-4 py-2 text-right"
              :class="stat.success_rate >= 0.95 ? 'text-green-500' : 'text-amber-500'"
            >
              {{ (stat.success_rate * 100).toFixed(1) }}%
            </td>
            <td class="px-4 py-2 text-right text-slate-600">{{ stat.avg_tokens }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="text-xs text-slate-400 text-center py-6">暂无数据</div>
    </div>

    <!-- 工具统计表 -->
    <div class="bg-white border border-slate-200 rounded-xl overflow-hidden">
      <div class="px-4 py-3 bg-slate-50 border-b border-slate-200">
        <span class="text-sm font-semibold text-slate-700">工具统计</span>
      </div>
      <table class="w-full text-sm" v-if="Object.keys(toolStats).length">
        <thead>
          <tr class="border-b border-slate-100 text-xs text-slate-500">
            <th class="text-left px-4 py-2">工具</th>
            <th class="text-right px-4 py-2">调用次数</th>
            <th class="text-right px-4 py-2">Avg 耗时</th>
            <th class="text-right px-4 py-2">成功率</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(stat, name) in toolStats"
            :key="name"
            class="border-b border-slate-50 hover:bg-slate-50"
          >
            <td class="px-4 py-2 text-slate-700 font-medium">{{ name }}</td>
            <td class="px-4 py-2 text-right text-slate-600">{{ stat.call_count }}</td>
            <td class="px-4 py-2 text-right text-slate-600">
              {{ stat.avg_duration_ms?.toFixed(0) }}ms
            </td>
            <td
              class="px-4 py-2 text-right"
              :class="stat.success_rate >= 0.95 ? 'text-green-500' : 'text-amber-500'"
            >
              {{ (stat.success_rate * 100).toFixed(1) }}%
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="text-xs text-slate-400 text-center py-6">暂无数据</div>
    </div>

    <!-- 慢 Trace -->
    <div
      class="bg-white border border-slate-200 rounded-xl overflow-hidden"
      v-if="slowTraces.length"
    >
      <div class="px-4 py-3 bg-slate-50 border-b border-slate-200">
        <span class="text-sm font-semibold text-slate-700">慢 Trace</span>
      </div>
      <div
        v-for="t in slowTraces"
        :key="t.trace_id"
        class="px-4 py-2 border-b border-slate-50 hover:bg-slate-50 cursor-pointer text-sm"
        @click="$emit('select-trace', t.trace_id)"
      >
        <span class="text-amber-500 font-medium">{{ (t.duration_ms / 1000).toFixed(1) }}s</span>
        <span class="text-slate-500 ml-2">{{ t.mode }}</span>
        <span class="text-slate-400 ml-2 truncate">{{ t.question_summary?.slice(0, 40) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AgentStats, ToolStats, LLMStats, SlowTraceItem } from '../../api/trace'

const props = defineProps<{
  agentStats: AgentStats
  toolStats: ToolStats
  llmStats: LLMStats
  slowTraces: SlowTraceItem[]
  agentDays?: number
  slowDays?: number
}>()

defineEmits<{ 'select-trace': [traceId: string] }>()

const overallAgentSuccess = computed(() => {
  const agents = Object.values(props.agentStats)
  if (!agents.length) return 0
  const avg = agents.reduce((s, a) => s + a.success_rate, 0) / agents.length
  return (avg * 100).toFixed(1)
})

const totalToolCalls = computed(() =>
  Object.values(props.toolStats).reduce((s, t) => s + t.call_count, 0),
)
</script>
