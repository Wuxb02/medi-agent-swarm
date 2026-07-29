<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import type { Citation } from '../../types'

const props = defineProps<{
  citations: Citation[]
  refNumbers: number[]
  anchorEl: HTMLElement | null
}>()

const emit = defineEmits<{
  close: []
}>()

const popoverRef = ref<HTMLElement | null>(null)
const popTop = ref(0)
const popLeft = ref(0)
const popVisible = ref(false)

const matchedCitations = computed(() => {
  return props.refNumbers
    .map((n) => props.citations.find((c) => c.index === n))
    .filter(Boolean) as Citation[]
})

function recalcPosition() {
  if (!props.anchorEl) return
  const rect = props.anchorEl.getBoundingClientRect()
  const popW = popoverRef.value?.offsetWidth ?? 320

  let left = rect.left - 100
  if (left < 4) left = 4
  if (left + popW > window.innerWidth - 4) left = window.innerWidth - popW - 4

  // 如果下方空间不够，显示在上方
  const spaceBelow = window.innerHeight - rect.bottom
  if (spaceBelow > 220 || spaceBelow > rect.top) {
    popTop.value = rect.bottom + 4
  } else {
    popTop.value = rect.top - 4
  }
  popLeft.value = left
}

let recalcTimer: ReturnType<typeof setTimeout> | null = null
function onScrollOrResize() {
  if (!recalcTimer) {
    recalcTimer = setTimeout(() => {
      recalcPosition()
      recalcTimer = null
    }, 0)
  }
}

function handleClickOutside(e: MouseEvent) {
  const target = e.target as Node
  // 点击 anchor 本身不关闭
  if (props.anchorEl?.contains(target)) return
  if (popoverRef.value && !popoverRef.value.contains(target)) {
    emit('close')
  }
}

watch(
  () => props.anchorEl,
  (el) => {
    if (el) {
      nextTick(() => {
        recalcPosition()
        popVisible.value = true
      })
    } else {
      popVisible.value = false
    }
  },
  { immediate: true },
)

onMounted(() => {
  document.addEventListener('mousedown', handleClickOutside, true)
  window.addEventListener('scroll', onScrollOrResize, true)
  window.addEventListener('resize', onScrollOrResize)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleClickOutside, true)
  window.removeEventListener('scroll', onScrollOrResize, true)
  window.removeEventListener('resize', onScrollOrResize)
})

function formatScore(score: number): string {
  return `${(score * 100).toFixed(0)}%`
}

defineExpose({ popoverRef })
</script>

<template>
  <Teleport to="body">
    <div
      ref="popoverRef"
      class="citation-popover fixed z-50 bg-white rounded-xl shadow-xl border border-slate-200 p-4 max-w-md transition-opacity duration-150"
      :style="{
        top: `${popTop}px`,
        left: `${popLeft}px`,
        opacity: popVisible ? 1 : 0,
        pointerEvents: popVisible ? 'auto' : 'none',
      }"
    >
      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-semibold text-slate-500 uppercase tracking-wide">引用来源</span>
        <span class="text-xs text-blue-500 font-medium"> [{{ refNumbers.join(', ') }}] </span>
      </div>
      <div
        v-for="cite in matchedCitations"
        :key="cite.index"
        class="py-2 first:pt-0 border-b border-slate-100 last:border-0"
      >
        <div class="flex items-center gap-2 mb-1">
          <span class="text-xs font-bold text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded"
            >[{{ cite.index }}]</span
          >
          <span class="text-xs text-slate-400">{{ formatScore(cite.score) }} 相关</span>
        </div>
        <div
          class="text-sm text-slate-700 leading-relaxed mb-1.5 max-h-48 overflow-y-auto bg-slate-50 rounded-md p-2 whitespace-pre-wrap break-words"
        >
          {{ cite.content || cite.snippet || '(无内容预览)' }}
        </div>
        <div class="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-400">
          <span v-if="cite.source">来源：{{ cite.source }}</span>
          <span v-if="cite.disease">疾病：{{ cite.disease }}</span>
          <span v-if="cite.type">类型：{{ cite.type }}</span>
          <span v-if="cite.filename" class="truncate max-w-[200px]">文件：{{ cite.filename }}</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>
