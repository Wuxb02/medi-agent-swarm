<template>
  <div class="bg-white border border-slate-200 rounded-xl p-4 max-h-[600px] overflow-y-auto">
    <h3 class="text-sm font-semibold text-slate-700 mb-3">树形结构</h3>
    <div v-if="flattenedNodes.length" class="text-sm">
      <div
        v-for="item in flattenedNodes"
        :key="item.id"
        class="flex items-center gap-1.5 py-1 cursor-pointer hover:bg-slate-50 rounded px-1 transition"
        :style="{ paddingLeft: item.depth * 18 + 4 + 'px' }"
        @click="$emit('select-node', item)"
      >
        <span class="w-2 h-2 rounded-full shrink-0" :class="dotColor(item)" />
        <span class="text-xs text-slate-600 truncate">{{ item.name || item.span_type }}</span>
        <span class="text-[10px] text-slate-400 ml-auto shrink-0">
          {{ item.duration_ms != null ? item.duration_ms.toFixed(0) + 'ms' : '' }}
        </span>
        <span v-if="item.status === 'error'" class="text-[10px] text-red-400">ERR</span>
      </div>
    </div>
    <div v-else class="text-xs text-slate-400 text-center py-8">无数据</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface FlatNode {
  id: string
  span_type: string
  name: string
  status: string
  duration_ms: number | null
  depth: number
}

const props = defineProps<{ treeData: any }>()
defineEmits<{ 'select-node': [node: any] }>()

function flatten(node: any, depth: number): FlatNode[] {
  if (!node) return []
  const items: FlatNode[] = [
    {
      id: node.id || '',
      span_type: node.span_type || '',
      name: node.name || '',
      status: node.status || 'ok',
      duration_ms: node.timing?.duration_ms ?? null,
      depth,
    },
  ]
  if (node.children) {
    for (const child of node.children) {
      items.push(...flatten(child, depth + 1))
    }
  }
  return items
}

const flattenedNodes = computed(() => (props.treeData ? flatten(props.treeData, 0) : []))

function dotColor(item: FlatNode): string {
  if (item.status === 'error') return 'bg-red-400'
  const map: Record<string, string> = {
    trace: 'bg-slate-400',
    stage: 'bg-blue-400',
    agent: 'bg-green-400',
    iteration: 'bg-purple-400',
    llm: 'bg-orange-400',
    tool: 'bg-cyan-400',
  }
  return map[item.span_type] || 'bg-slate-400'
}
</script>
