<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useChatStore } from '../../stores/chat'

const route = useRoute()
const chatStore = useChatStore()

const titles: Record<string, string> = {
  Chat: '智能问答',
  ChatSession: '智能问答',
  Knowledge: '知识库',
  Sessions: '历史会话',
  Dashboard: '仪表盘',
}

const title = route.name ? titles[route.name as string] || 'MediZJ' : 'MediZJ'
</script>

<template>
  <header class="h-14 bg-white border-b border-slate-200 flex items-center justify-between px-6 shrink-0">
    <h1 class="text-lg font-semibold text-slate-800">{{ title }}</h1>
    <div v-if="route.name === 'Chat' || route.name === 'ChatSession'" class="flex items-center gap-3">
      <label class="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
        <span>Swarm 模式</span>
        <button
          @click="chatStore.swarmMode = !chatStore.swarmMode"
          class="relative w-10 h-5 rounded-full transition-colors"
          :class="chatStore.swarmMode ? 'bg-blue-500' : 'bg-slate-300'"
        >
          <span
            class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform"
            :class="chatStore.swarmMode ? 'translate-x-5' : 'translate-x-0'"
          />
        </button>
      </label>
    </div>
  </header>
</template>
