<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-7xl mx-auto space-y-6">
      <!-- Loading -->
      <div v-if="loading" class="text-center py-12 text-slate-400">加载中...</div>

      <!-- Error -->
      <div v-else-if="error" class="text-center py-12">
        <p class="text-red-500 mb-3">{{ error }}</p>
        <button @click="refresh" class="text-sm text-blue-500 hover:underline">重试</button>
      </div>

      <!-- 详情模式 -->
      <template v-else-if="selectedTraceId">
        <!-- 返回栏 -->
        <div class="flex items-center gap-4">
          <button
            @click="selectedTraceId = null"
            class="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700 transition"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
            </svg>
            返回列表
          </button>
          <span class="text-xs text-slate-400 font-mono">{{ selectedTraceId.slice(0, 20) }}...</span>
        </div>

        <TraceWaterfall
          :spans="waterfallSpans"
          :totalDurationMs="waterfallTotalMs"
          :selectedSpanId="selectedSpan?.id ?? null"
          @select-span="selectTopSpan"
        />
        <div class="grid grid-cols-1 gap-6">
          <SpanDetail
            v-if="selectedSpan"
            :span="selectedSpan"
            :allSpans="waterfallSpans"
            :canGoBack="spanNavStack.length > 1"
            @close="closeSpanDetail"
            @back="goBack"
            @select-span="navigateToChild"
          />
          <div v-else class="bg-white border border-slate-200 rounded-xl p-5 flex items-center justify-center">
            <p class="text-sm text-slate-400">点击 waterfall 节点查看详情</p>
          </div>
        </div>
      </template>

      <!-- 列表 + 统计模式 -->
      <template v-else>
        <TraceStats
          :agentStats="agentStats"
          :toolStats="toolStats"
          :llmStats="llmStats"
          :slowTraces="slowTraces"
          :agentDays="7"
          :slowDays="7"
          @select-trace="selectedTraceId = $event"
        />

        <!-- Trace 列表 -->
        <div class="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div class="px-4 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
            <span class="text-sm font-semibold text-slate-700">最近 Trace</span>
            <span class="text-xs text-slate-400">{{ totalTraces }} 条</span>
          </div>
          <table class="w-full text-sm" v-if="traces.length">
            <thead>
              <tr class="border-b border-slate-100 text-xs text-slate-500">
                <th class="text-left px-4 py-2">Session</th>
                <th class="text-left px-4 py-2">创建时间</th>
                <th class="text-right px-4 py-2">耗时</th>
                <th class="text-center px-4 py-2">模式</th>
                <th class="text-center px-4 py-2">Span 数</th>
                <th class="text-right px-4 py-2">Tokens</th>
                <th class="text-left px-4 py-2">问题摘要</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="t in traces"
                :key="t.trace_id"
                class="border-b border-slate-50 hover:bg-slate-50 cursor-pointer transition"
                @click="selectedTraceId = t.trace_id"
              >
                <td class="px-4 py-2 font-mono text-xs text-slate-500">{{ t.trace_id.slice(0, 16) }}...</td>
                <td class="px-4 py-2 text-xs text-slate-500 whitespace-nowrap">{{ formatTime(t.start_time) }}</td>
                <td class="px-4 py-2 text-right">
                  <span :class="(t.duration_ms || 0) > 30000 ? 'text-amber-500 font-medium' : 'text-slate-600'">
                    {{ t.duration_ms ? (t.duration_ms / 1000).toFixed(1) + 's' : '-' }}
                  </span>
                </td>
                <td class="px-4 py-2 text-center">
                  <span class="text-xs px-2 py-0.5 rounded-full" :class="modeBadge(t.mode)">
                    {{ modeLabel(t.mode) }}
                  </span>
                </td>
                <td class="px-4 py-2 text-center text-slate-600">{{ t.span_count }}</td>
                <td class="px-4 py-2 text-right text-slate-600">{{ t.total_tokens.toLocaleString() }}</td>
                <td class="px-4 py-2 text-slate-500 truncate max-w-[240px]">{{ t.question_summary }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="text-sm text-slate-400 text-center py-8">
            暂无 trace 数据。发送一条消息后将自动生成。
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  getTraces, getTraceWaterfall,
  getAgentStats, getToolStats, getLLMStats, getSlowTraces,
  type TraceSummary, type WaterfallSpan,
  type AgentStats, type ToolStats, type LLMStats, type SlowTraceItem,
} from '../api/trace'
import TraceWaterfall from '../components/trace/TraceWaterfall.vue'
import SpanDetail from '../components/trace/SpanDetail.vue'
import TraceStats from '../components/trace/TraceStats.vue'

const loading = ref(true)
const error = ref<string | null>(null)

const traces = ref<TraceSummary[]>([])
const totalTraces = ref(0)
const agentStats = ref<AgentStats>({})
const toolStats = ref<ToolStats>({})
const llmStats = ref<LLMStats>({
  call_count: 0, avg_latency_ms: 0, p50_ms: 0, p90_ms: 0,
  avg_prompt_tokens: 0, avg_completion_tokens: 0,
  total_prompt_tokens: 0, total_completion_tokens: 0,
})
const slowTraces = ref<SlowTraceItem[]>([])

const selectedTraceId = ref<string | null>(null)
const waterfallSpans = ref<WaterfallSpan[]>([])
const waterfallTotalMs = ref(0)
const spanNavStack = ref<WaterfallSpan[]>([])

const selectedSpan = computed(() =>
  spanNavStack.value.length > 0 ? spanNavStack.value[spanNavStack.value.length - 1] : null
)

function navigateToChild(span: WaterfallSpan) {
  spanNavStack.value.push(span)
}

function selectTopSpan(span: WaterfallSpan) {
  spanNavStack.value = [span]
}

function goBack() {
  if (spanNavStack.value.length > 1) {
    spanNavStack.value.pop()
  }
}

function closeSpanDetail() {
  spanNavStack.value = []
}

async function loadList() {
  loading.value = true
  error.value = null
  try {
    const [traceList, agents, tools, llm, slow] = await Promise.all([
      getTraces(50, 0),
      getAgentStats(7),
      getToolStats(7),
      getLLMStats(7),
      getSlowTraces(30000, 10),
    ])
    traces.value = traceList.traces
    totalTraces.value = traceList.total
    agentStats.value = agents || {}
    toolStats.value = tools || {}
    llmStats.value = llm || llmStats.value
    slowTraces.value = slow || []
  } catch (e: any) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadDetail(traceId: string) {
  loading.value = true
  error.value = null
  try {
    const waterfall = await getTraceWaterfall(traceId)
    waterfallSpans.value = waterfall.spans
    waterfallTotalMs.value = waterfall.total_duration_ms
    spanNavStack.value = []
  } catch (e: any) {
    error.value = e.message || '加载失败'
    selectedTraceId.value = null
  } finally {
    loading.value = false
  }
}

function refresh() {
  if (selectedTraceId.value) {
    loadDetail(selectedTraceId.value)
  } else {
    loadList()
  }
}

watch(selectedTraceId, (newId) => {
  if (newId) loadDetail(newId)
})

function modeLabel(mode: string): string {
  const map: Record<string, string> = {
    single_agent: '单Agent', swarm: 'Swarm', fallback: '降级',
  }
  return map[mode] || mode
}

function modeBadge(mode: string): string {
  const map: Record<string, string> = {
    single_agent: 'bg-blue-50 text-blue-600',
    swarm: 'bg-purple-50 text-purple-600',
    fallback: 'bg-amber-50 text-amber-600',
  }
  return map[mode] || 'bg-slate-50 text-slate-600'
}

function formatTime(isoStr: string): string {
  if (!isoStr) return '-'
  const d = new Date(isoStr)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const route = useRoute()

onMounted(() => {
  const tid = route.params.traceId as string | undefined
  if (tid) {
    selectedTraceId.value = tid
  } else {
    loadList()
  }
})
</script>