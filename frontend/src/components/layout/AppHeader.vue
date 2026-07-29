<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useChatStore } from '../../stores/chat'

const route = useRoute()
const chatStore = useChatStore()

const pageTitles: Record<string, string> = {
  Chat: '智能问答',
  Knowledge: '知识库',
  Dashboard: '仪表盘',
  Traces: '轨迹',
  TraceDetail: '轨迹详情',
  Personal: '个人中心',
}

const title = computed(() => {
  const name = route.name as string
  if (name === 'ChatSession') {
    return chatStore.sessionTitle || '智能问答'
  }
  return pageTitles[name] || 'MediZJ'
})
</script>

<template>
  <header
    class="h-14 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0"
  >
    <h1 class="text-lg font-semibold text-slate-800">{{ title }}</h1>
  </header>
</template>
