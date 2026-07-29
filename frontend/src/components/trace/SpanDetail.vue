<template>
  <div class="bg-white border border-slate-200 rounded-xl p-5">
    <div class="flex items-center justify-between mb-4">
      <div class="flex items-center gap-2">
        <button
          v-if="canGoBack"
          @click="$emit('back')"
          class="text-slate-400 hover:text-slate-600 transition"
          title="返回上级"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M15 19l-7-7 7-7"
            />
          </svg>
        </button>
        <h3 class="text-sm font-semibold text-slate-700">Span 详情</h3>
      </div>
      <button @click="$emit('close')" class="text-slate-400 hover:text-slate-600">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>
    </div>

    <div class="space-y-2 text-sm">
      <div class="flex justify-between">
        <span class="text-slate-500">类型</span>
        <span class="text-slate-800 font-medium">{{ spanTypeLabel }}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-slate-500">名称</span>
        <span class="text-slate-800">{{ span.name }}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-slate-500">状态</span>
        <span :class="statusClass">{{ span.status }}</span>
      </div>
      <div class="flex justify-between">
        <span class="text-slate-500">耗时</span>
        <span class="text-slate-800">{{ (span.duration_ms || 0).toFixed(1) }}ms</span>
      </div>
      <div v-if="span.error_message" class="flex justify-between">
        <span class="text-slate-500">错误</span>
        <span class="text-red-500 text-xs">{{ span.error_message }}</span>
      </div>

      <!-- 子节点列表 -->
      <div v-if="children.length" class="border-t border-slate-100 pt-2 mt-2">
        <span class="text-xs text-slate-400">子节点 ({{ children.length }})</span>
        <div
          v-for="child in children"
          :key="child.id"
          class="flex items-center gap-2 mt-1 py-1 px-2 rounded hover:bg-slate-50 cursor-pointer text-xs"
          @click="$emit('select-span', child)"
        >
          <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="childDotColor(child)" />
          <span class="text-slate-600 truncate">{{ child.name }}</span>
          <span class="text-slate-400 ml-auto shrink-0"
            >{{ (child.duration_ms || 0).toFixed(0) }}ms</span
          >
        </div>
      </div>

      <!-- LLM Attrs -->
      <template v-if="span.attributes.llm">
        <div class="border-t border-slate-100 pt-2 mt-2">
          <span class="text-xs text-slate-400">LLM 调用</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-500">模型</span>
          <span class="text-slate-800">{{ span.attributes.llm.model }}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-500">Prompt Tokens</span>
          <span class="text-slate-800">{{ span.attributes.llm.prompt_tokens }}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-500">Completion Tokens</span>
          <span class="text-slate-800">{{ span.attributes.llm.completion_tokens }}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-500">Total Tokens</span>
          <span class="text-slate-800">{{ span.attributes.llm.total_tokens }}</span>
        </div>
        <div v-if="span.attributes.llm.finish_reason" class="flex justify-between">
          <span class="text-slate-500">Finish</span>
          <span class="text-slate-800">{{ span.attributes.llm.finish_reason }}</span>
        </div>
        <div v-if="span.attributes.llm.output_content_summary" class="mt-2">
          <span class="text-xs text-slate-400">输出内容</span>
          <pre
            class="text-xs text-slate-600 mt-0.5 bg-slate-50 rounded p-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-all"
            >{{ formatContent(span.attributes.llm.output_content_summary) }}</pre>
        </div>
      </template>

      <!-- Tool Attrs -->
      <template v-if="span.attributes.tool">
        <div class="border-t border-slate-100 pt-2 mt-2">
          <span class="text-xs text-slate-400">工具调用</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-500">工具名</span>
          <span class="text-slate-800">{{ span.attributes.tool.tool_name }}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-500">成功</span>
          <span :class="span.attributes.tool.success ? 'text-green-500' : 'text-red-500'">
            {{ span.attributes.tool.success ? '是' : '否' }}
          </span>
        </div>
        <div v-if="span.attributes.tool.result_summary" class="mt-1">
          <span class="text-xs text-slate-400">结果摘要</span>
          <pre
            class="text-xs text-slate-600 mt-0.5 bg-slate-50 rounded p-2 max-h-60 overflow-y-auto whitespace-pre-wrap break-all"
            >{{ formatContent(span.attributes.tool.result_summary) }}</pre>
        </div>
      </template>

      <!-- Agent Attrs -->
      <template v-if="span.attributes.agent">
        <div class="border-t border-slate-100 pt-2 mt-2">
          <span class="text-xs text-slate-400">Agent</span>
        </div>
        <div class="flex justify-between">
          <span class="text-slate-500">Agent ID</span>
          <span class="text-slate-800">{{ span.attributes.agent.agent_id }}</span>
        </div>
        <div class="flex justify-between" v-if="span.attributes.agent.iteration_count">
          <span class="text-slate-500">迭代次数</span>
          <span class="text-slate-800">{{ span.attributes.agent.iteration_count }}</span>
        </div>
        <div class="flex justify-between" v-if="span.attributes.agent.total_tokens">
          <span class="text-slate-500">Total Tokens</span>
          <span class="text-slate-800">{{ span.attributes.agent.total_tokens }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { WaterfallSpan } from '../../api/trace'

const props = defineProps<{
  span: WaterfallSpan
  allSpans?: WaterfallSpan[]
  canGoBack?: boolean
}>()

defineEmits<{ close: []; back: []; 'select-span': [span: WaterfallSpan] }>()

const typeLabels: Record<string, string> = {
  trace: 'Trace',
  stage: 'Stage',
  agent: 'Agent',
  iteration: 'Iteration',
  llm: 'LLM',
  tool: 'Tool',
}

const spanTypeLabel = computed(() => typeLabels[props.span.span_type] || props.span.span_type)

const children = computed(
  () => props.allSpans?.filter((s: WaterfallSpan) => s.parent_id === props.span.id) || [],
)

function childDotColor(s: WaterfallSpan): string {
  if (s.status === 'error') return 'bg-red-400'
  const map: Record<string, string> = {
    stage: 'bg-blue-400',
    agent: 'bg-green-400',
    iteration: 'bg-purple-400',
    llm: 'bg-orange-400',
    tool: 'bg-cyan-400',
    trace: 'bg-slate-400',
  }
  return map[s.span_type] || 'bg-slate-400'
}

const statusClass = computed(() =>
  props.span.status === 'error'
    ? 'text-red-500 font-medium'
    : props.span.status === 'timeout'
      ? 'text-amber-500 font-medium'
      : 'text-green-500',
)

function formatContent(str: string | undefined | null): string {
  if (!str) return ''
  try {
    const parsed = JSON.parse(str)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return str
  }
}
</script>
