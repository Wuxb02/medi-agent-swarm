<script setup lang="ts">
import { ref, watch, computed, onUnmounted } from 'vue'
import type { ReasoningStatus, ToolStep } from '../../types'

const props = defineProps<{
  thinking: string
  agentId: string
  iteration: number
  toolSteps: ToolStep[]
  elapsedSeconds?: number
  isCollapsed: boolean
  label?: string
  status?: ReasoningStatus
}>()

// 标题显示文本：优先用 label，否则走默认 "迭代N"
const displayLabel = computed(() => {
  if (props.label) return props.label
  return `迭代${props.iteration}`
})

const statusLabel = computed(() => {
  const labels: Record<ReasoningStatus, string> = {
    running: '进行中',
    waiting: '等待回答',
    completed: '已完成',
    skipped: '已跳过',
    failed: '失败',
  }
  return props.status ? labels[props.status] : ''
})

// 新版事件以 status 判断是否仍在执行；旧版事件继续兼容 isCollapsed。
const isProcessing = computed(() =>
  props.status ? props.status === 'running' : !props.isCollapsed,
)

const isExpanded = ref(!props.isCollapsed)

watch(
  () => props.isCollapsed,
  (val) => {
    isExpanded.value = !val
  },
)

function toggle() {
  isExpanded.value = !isExpanded.value
}

const expandedSteps = ref<Record<number, boolean>>({})

function toggleStep(idx: number) {
  expandedSteps.value[idx] = !expandedSteps.value[idx]
}

// 实时耗时：迭代进行中时实时计时
const liveElapsed = ref(0)
const createdAt = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

function startTimer() {
  if (timer) return
  createdAt.value = Date.now()
  timer = setInterval(() => {
    liveElapsed.value = (Date.now() - createdAt.value) / 1000
  }, 200)
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

// 执行中启动计时，完成、等待、跳过或失败时停止。
watch(
  isProcessing,
  (processing) => {
    if (processing) {
      startTimer()
    } else {
      stopTimer()
    }
  },
  { immediate: true },
)

onUnmounted(() => stopTimer())

const displayElapsed = computed(() => {
  if (props.elapsedSeconds != null) return props.elapsedSeconds.toFixed(1)
  return liveElapsed.value.toFixed(1)
})
</script>

<template>
  <div class="border border-slate-200 rounded overflow-hidden text-xs">
    <!-- 标题栏 -->
    <button
      @click="toggle"
      class="w-full flex items-center gap-2 px-3 py-1.5 bg-white hover:bg-slate-50 transition text-left"
    >
      <!-- 展开/折叠箭头 -->
      <svg
        class="w-3 h-3 text-slate-400 transition-transform shrink-0"
        :class="{ 'rotate-90': isExpanded }"
        fill="currentColor"
        viewBox="0 0 20 20"
      >
        <path
          fill-rule="evenodd"
          d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
        />
      </svg>

      <span class="text-slate-500">{{ displayLabel }}</span>
      <span
        v-if="statusLabel"
        class="rounded px-1.5 py-0.5 text-[10px]"
        :class="{
          'bg-blue-50 text-blue-600': status === 'running',
          'bg-amber-50 text-amber-600': status === 'waiting',
          'bg-green-50 text-green-600': status === 'completed',
          'bg-slate-100 text-slate-500': status === 'skipped',
          'bg-red-50 text-red-600': status === 'failed',
        }"
      >
        {{ statusLabel }}
      </span>

      <!-- 推理中：跳动点动画 -->
      <span v-if="isProcessing" class="ml-auto flex gap-0.5">
        <span
          class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
          style="animation-delay: 0ms"
        />
        <span
          class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
          style="animation-delay: 150ms"
        />
        <span
          class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
          style="animation-delay: 300ms"
        />
      </span>
    </button>

    <!-- 展开内容 -->
    <div v-if="isExpanded" class="border-t border-slate-100">
      <!-- 推理文本 -->
      <div v-if="thinking" class="px-3 py-2 text-slate-600 leading-relaxed whitespace-pre-wrap">
        {{ thinking }}
      </div>

      <!-- 工具调用步骤 -->
      <div v-if="toolSteps.length > 0" class="px-3 py-2 space-y-1.5 border-t border-slate-100">
        <div
          v-for="(step, idx) in toolSteps"
          :key="idx"
          class="border border-slate-200 rounded overflow-hidden"
        >
          <button
            @click="toggleStep(idx)"
            class="w-full flex items-center gap-2 px-2.5 py-1.5 bg-slate-50 hover:bg-slate-100 transition text-left"
          >
            <svg
              class="w-2.5 h-2.5 text-slate-400 transition-transform shrink-0"
              :class="{ 'rotate-90': expandedSteps[idx] }"
              fill="currentColor"
              viewBox="0 0 20 20"
            >
              <path
                fill-rule="evenodd"
                d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
              />
            </svg>
            <span class="font-medium text-slate-600">{{ step.toolName }}</span>
            <span v-if="step.status === 'waiting'" class="text-amber-500 ml-auto">等待回答</span>
            <span v-else-if="step.success" class="text-green-500 ml-auto">&#x2713;</span>
            <span v-else class="text-red-500 ml-auto">&#x2717;</span>
          </button>
          <div
            v-if="expandedSteps[idx]"
            class="px-2.5 py-1.5 text-slate-500 border-t border-slate-100 space-y-1"
          >
            <div v-if="step.arguments && Object.keys(step.arguments).length > 0">
              <span class="text-slate-400">参数：</span>
              <code class="text-[10px] bg-slate-100 px-1 py-0.5 rounded">{{
                JSON.stringify(step.arguments)
              }}</code>
            </div>
            <div>
              <span class="text-slate-400">结果：</span>
              <div
                class="mt-0.5 bg-slate-50 rounded px-2 py-1 text-[11px] leading-relaxed max-h-32 overflow-auto whitespace-pre-wrap"
              >
                {{ step.result }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 推理耗时 -->
      <div class="px-3 py-1.5 text-slate-400 border-t border-slate-100 text-right">
        耗时 {{ displayElapsed }}s
      </div>
    </div>
  </div>
</template>
