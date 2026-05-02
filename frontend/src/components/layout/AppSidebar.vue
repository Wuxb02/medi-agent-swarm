<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useChatStore } from '../../stores/chat'

const router = useRouter()
const route = useRoute()
const chatStore = useChatStore()

const navItems = [
  { path: '/chat', label: '智能问答', icon: 'M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z' },
  { path: '/knowledge', label: '知识库', icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253' },
  { path: '/sessions', label: '历史会话', icon: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z' },
  { path: '/dashboard', label: '仪表盘', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z' },
]

function newChat() {
  chatStore.clearChat()
  router.push('/chat')
}
</script>

<template>
  <aside class="w-60 bg-slate-800 text-slate-200 flex flex-col shrink-0">
    <!-- Logo -->
    <div class="p-4 border-b border-slate-700">
      <div class="flex items-center gap-2">
        <div class="w-8 h-8 bg-blue-500 rounded-lg flex items-center justify-center text-white font-bold text-sm">M</div>
        <div>
          <div class="text-sm font-semibold text-white">MediZJ</div>
          <div class="text-xs text-slate-400">多智能体医疗助手</div>
        </div>
      </div>
    </div>

    <!-- 新建会话 -->
    <div class="p-3">
      <button
        @click="newChat"
        class="w-full py-2 px-3 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition flex items-center justify-center gap-2"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        新建会话
      </button>
    </div>

    <!-- 导航 -->
    <nav class="flex-1 px-3 space-y-1">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg transition"
        :class="route.path.startsWith(item.path)
          ? 'bg-slate-700 text-white'
          : 'text-slate-300 hover:bg-slate-700/50 hover:text-white'"
      >
        <svg class="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" :d="item.icon" />
        </svg>
        {{ item.label }}
      </router-link>
    </nav>

    <!-- 底部信息 -->
    <div class="p-3 border-t border-slate-700 text-xs text-slate-500">
      MediZJ Agent Swarm v0.1.0
    </div>
  </aside>
</template>
