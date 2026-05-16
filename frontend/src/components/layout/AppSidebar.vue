<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useChatStore } from '../../stores/chat'
import { getSessions, deleteSession } from '../../api/session'
import type { SessionItem } from '../../types'

const router = useRouter()
const route = useRoute()
const chatStore = useChatStore()

const navItems = [
  { path: '/chat', label: '智能问答', icon: 'M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z' },
  { path: '/knowledge', label: '知识库', icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253' },
  { path: '/dashboard', label: '仪表盘', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z' },
]

const sessions = ref<SessionItem[]>([])
const loading = ref(false)

async function loadSessions() {
  if (loading.value) return
  loading.value = true
  try {
    sessions.value = await getSessions(200, 0)
  } catch (e) {
    console.error('Failed to load sessions:', e)
  } finally {
    loading.value = false
  }
}

function openSession(sessionId: string) {
  router.push(`/chat/${sessionId}`)
}

async function handleDelete(sessionId: string, e: Event) {
  e.stopPropagation()
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter(s => s.session_id !== sessionId)
    if (route.params.sessionId === sessionId) {
      chatStore.clearChat()
      router.push('/chat')
    }
  } catch (e) {
    console.error('Delete failed:', e)
  }
}

function newChat() {
  chatStore.clearChat()
  router.push('/chat')
}

function formatTime(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    const now = new Date()
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const yesterday = new Date(today.getTime() - 86400000)
    const dateOnly = new Date(d.getFullYear(), d.getMonth(), d.getDate())

    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')

    if (dateOnly.getTime() === today.getTime()) return `${hh}:${mm}`
    if (dateOnly.getTime() === yesterday.getTime()) return `昨天 ${hh}:${mm}`
    return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return dateStr
  }
}

function truncate(str: string, len: number): string {
  if (!str) return '未命名会话'
  return str.length > len ? str.slice(0, len) + '…' : str
}

onMounted(() => {
  loadSessions()
})

watch(() => chatStore.sessionId, (newId, oldId) => {
  if (newId && !oldId) {
    loadSessions()
  }
})
</script>

<template>
  <aside class="w-60 h-screen bg-slate-800 text-slate-200 flex flex-col shrink-0 overflow-hidden">
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
    <nav class="px-3 space-y-1">
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

    <!-- 最近会话列表 -->
    <div class="group/sessions flex-1 flex flex-col min-h-0 mt-2 px-3 overflow-hidden">
      <div class="shrink-0 px-3 pt-1.5 pb-3 text-sm text-slate-400 font-medium">最近会话</div>
      <div
        class="flex-1 min-h-0 overflow-y-auto pb-2 scrollbar-thin relative"
      >
        <div
          v-for="s in sessions"
          :key="s.session_id"
          @click="openSession(s.session_id)"
          class="group flex items-center justify-between px-3 py-2 text-sm rounded-lg cursor-pointer transition"
          :class="route.params.sessionId === s.session_id
            ? 'bg-slate-600 text-white'
            : 'text-slate-300 hover:bg-slate-700/50 hover:text-white'"
        >
          <div class="flex-1 min-w-0">
            <div class="truncate">{{ truncate(s.first_question, 18) }}</div>
          </div>
          <div class="flex items-center gap-1 shrink-0 ml-1">
            <span class="text-xs text-slate-500">{{ formatTime(s.created_at) }}</span>
            <button
              @click="handleDelete(s.session_id, $event)"
              class="p-0.5 text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
        <div v-if="loading" class="sticky bottom-0 text-center py-1.5 text-xs text-slate-500 bg-slate-800/90 backdrop-blur-sm">加载中...</div>
      </div>
    </div>

    <!-- 底部：个人中心 -->
    <div class="p-3 border-t border-slate-700">
      <router-link
        to="/personal"
        class="flex items-center gap-3 px-3 py-2 text-sm rounded-lg transition"
        :class="route.path === '/personal'
          ? 'bg-slate-700 text-white'
          : 'text-slate-400 hover:bg-slate-700/50 hover:text-white'"
      >
        <svg class="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
        </svg>
        个人中心
      </router-link>
    </div>

    <!-- 版本信息 -->
    <div class="px-3 pb-3 text-xs text-slate-600">
      MediZJ Agent Swarm v0.1.0
    </div>
  </aside>
</template>
