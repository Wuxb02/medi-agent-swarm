<template>
  <div class="bg-white border border-slate-200 rounded-xl overflow-hidden">
    <div class="px-4 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
      <span class="text-sm font-semibold text-slate-700">Waterfall 时序图</span>
      <div class="flex items-center gap-3">
        <button
          @click="expandAll"
          class="text-xs text-slate-400 hover:text-slate-600 transition"
          title="展开全部"
        >
          展开全部
        </button>
        <button
          @click="collapseAll"
          class="text-xs text-slate-400 hover:text-slate-600 transition"
          title="折叠全部"
        >
          折叠全部
        </button>
        <span class="text-xs text-slate-400">总耗时 {{ totalDurationMs.toFixed(0) }}ms · {{ spans.length }} spans</span>
      </div>
    </div>
    <div class="px-4 py-2 bg-slate-50 border-b border-slate-100 flex items-center gap-4 text-[11px] text-slate-500">
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-sm bg-slate-300" /> Trace</span>
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-sm bg-blue-300" /> Stage</span>
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-sm bg-green-300" /> Agent</span>
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-sm bg-purple-300" /> Iteration</span>
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-sm bg-orange-300" /> LLM</span>
      <span class="flex items-center gap-1"><span class="w-2.5 h-2.5 rounded-sm bg-cyan-300" /> Tool</span>
      <span class="text-slate-300">|</span>
      <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-red-400" /> 错误</span>
    </div>
    <div class="overflow-x-auto">
      <div class="min-w-[700px]">
        <!-- Ruler row -->
        <div class="flex items-end border-b border-slate-200 px-4 pt-1">
          <div class="w-48 shrink-0" />
          <div class="flex-1 h-5 relative">
            <div
              v-for="tick in timeTicks"
              :key="tick.offset"
              class="absolute top-0 w-px h-2 bg-slate-300"
              :style="{ left: tick.pct + '%' }"
            />
            <div
              v-for="tick in timeTicks"
              :key="'l'+tick.offset"
              class="absolute text-[10px] text-slate-400 -translate-x-1/2 whitespace-nowrap"
              :style="{ left: tick.pct + '%', top: '3px' }"
            >
              {{ tick.label }}
            </div>
          </div>
          <div class="w-16 ml-3 shrink-0" />
        </div>

        <!-- Span rows -->
        <div
          v-for="row in flattenedRows"
          :key="row.span.id"
          class="flex items-center border-b border-slate-50 hover:bg-slate-50 cursor-pointer transition px-4 py-1.5"
          :class="{ 'bg-blue-50/30': selectedSpan?.id === row.span.id }"
          :style="{ paddingLeft: (row.depth * 16 + 16) + 'px' }"
          @click="selectSpan(row.span)"
        >
          <!-- expand/collapse toggle -->
          <span
            v-if="row.hasChildren"
            @click.stop="toggleCollapse(row.span.id)"
            class="w-4 h-4 flex items-center justify-center text-[10px] text-slate-400 hover:text-slate-600 cursor-pointer select-none shrink-0 mr-1"
          >{{ isCollapsed(row.span.id) ? '▶' : '▼' }}</span>
          <span v-else class="w-4 shrink-0 mr-1" />
          <!-- Name -->
          <div class="flex items-center gap-2 min-w-0" style="width: 200px;">
            <span
              class="w-2 h-2 rounded-full shrink-0"
              :class="dotColor(row.span)"
              :title="row.span.status"
            />
            <span class="text-xs text-slate-600 truncate">{{ row.span.name }}</span>
          </div>
          <!-- Time bar -->
          <div class="flex-1 h-5 relative">
            <div class="absolute inset-y-0 w-full bg-slate-100 rounded-sm" />
            <div
              class="absolute inset-y-0 rounded-sm transition"
              :class="barColor(row.span)"
              :style="{
                left: barLeft(row.span) + '%',
                width: Math.max(barWidth(row.span), 0.5) + '%',
              }"
            />
          </div>
          <span class="text-xs text-slate-500 w-16 text-right shrink-0 ml-3">
            {{ row.span.duration_ms.toFixed(0) }}ms
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { WaterfallSpan } from '../../api/trace'

// ---- Props & Emits ----

const props = defineProps<{
  spans: WaterfallSpan[]
  totalDurationMs: number
  selectedSpanId?: string | null
}>()

const emit = defineEmits<{ 'select-span': [span: WaterfallSpan] }>()

// ---- Tree Node ----

interface TreeNode {
  span: WaterfallSpan
  children: TreeNode[]
}

function buildTree(spans: WaterfallSpan[]): TreeNode[] {
  const map = new Map<string, TreeNode>()
  const roots: TreeNode[] = []

  for (const s of spans) {
    map.set(s.id, { span: s, children: [] })
  }

  for (const s of spans) {
    const node = map.get(s.id)!
    if (s.parent_id && map.has(s.parent_id)) {
      map.get(s.parent_id)!.children.push(node)
    } else {
      roots.push(node)
    }
  }

  return roots
}

// ---- Collapse state ----

const collapsedIds = ref<Set<string>>(new Set())
const selectedSpan = ref<WaterfallSpan | null>(null)

watch(() => props.selectedSpanId, (newId) => {
  if (newId) {
    const found = props.spans.find((s: WaterfallSpan) => s.id === newId)
    if (found) selectedSpan.value = found
  } else if (newId === null) {
    selectedSpan.value = null
  }
})

function isCollapsed(id: string): boolean {
  return collapsedIds.value.has(id)
}

function toggleCollapse(id: string) {
  const next = new Set(collapsedIds.value)
  if (next.has(id)) {
    next.delete(id)
  } else {
    next.add(id)
  }
  collapsedIds.value = next
}

function expandAll() {
  collapsedIds.value = new Set()
}

function collapseAll() {
  const ids = new Set<string>()
  for (const root of tree.value) {
    addAllDescendants(root, ids)
  }
  collapsedIds.value = ids
}

function addAllDescendants(node: TreeNode, ids: Set<string>) {
  if (node.children.length > 0) {
    ids.add(node.span.id)
    for (const child of node.children) {
      addAllDescendants(child, ids)
    }
  }
}

// ---- Flatten tree to visible rows ----

interface FlatRow {
  span: WaterfallSpan
  depth: number
  hasChildren: boolean
}

const tree = computed(() => buildTree(props.spans))

const flattenedRows = computed(() => {
  const rows: FlatRow[] = []
  flattenTree(tree.value, 0, rows)
  return rows
})

function flattenTree(nodes: TreeNode[], depth: number, rows: FlatRow[]) {
  for (const node of nodes) {
    const hasChildren = node.children.length > 0

    rows.push({
      span: node.span,
      depth,
      hasChildren,
    })

    if (!collapsedIds.value.has(node.span.id) && hasChildren) {
      flattenTree(node.children, depth + 1, rows)
    }
  }
}

// ---- Time helpers ----

const timeTicks = computed(() => {
  const total = props.totalDurationMs || 1
  const count = 10
  return Array.from({ length: count + 1 }, (_, i) => {
    const ms = (total / count) * i
    return {
      offset: ms,
      pct: (ms / total) * 100,
      label: ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : ms.toFixed(0) + 'ms',
    }
  })
})

function barLeft(span: WaterfallSpan): number {
  return (span.start_offset_ms / (props.totalDurationMs || 1)) * 100
}

function barWidth(span: WaterfallSpan): number {
  return (span.duration_ms / (props.totalDurationMs || 1)) * 100
}

// ---- Colors ----

function dotColor(span: WaterfallSpan): string {
  if (span.status === 'error') return 'bg-red-400'
  if (span.status === 'timeout') return 'bg-amber-400'
  const map: Record<string, string> = {
    stage: 'bg-blue-400',
    agent: 'bg-green-400',
    iteration: 'bg-purple-400',
    llm: 'bg-orange-400',
    tool: 'bg-cyan-400',
    trace: 'bg-slate-400',
  }
  return map[span.span_type] || 'bg-slate-400'
}

function barColor(span: WaterfallSpan): string {
  if (span.status === 'error') return 'bg-red-200'
  if (span.status === 'timeout') return 'bg-amber-200'
  const map: Record<string, string> = {
    stage: 'bg-blue-300',
    agent: 'bg-green-300',
    iteration: 'bg-purple-300',
    llm: 'bg-orange-300',
    tool: 'bg-cyan-300',
    trace: 'bg-slate-300',
  }
  return map[span.span_type] || 'bg-slate-300'
}

// ---- Events ----

function selectSpan(span: WaterfallSpan) {
  selectedSpan.value = span
  emit('select-span', span)
}
</script>