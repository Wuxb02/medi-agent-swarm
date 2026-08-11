<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useChatStore } from '../../stores/chat'
import { useAuthStore } from '../../stores/auth'
import { getSessions, deleteSession } from '../../api/session'
import { getPersonalInfo } from '../../api/personal'
import type { SessionItem } from '../../types'

const router = useRouter()
const route = useRoute()
const chatStore = useChatStore()
const auth = useAuthStore()

const navItems = [
  {
    path: '/chat',
    label: '智能问答',
    icon: 'M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z',
  },
  {
    path: '/knowledge',
    label: '知识库',
    icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253',
  },
  {
    path: '/dashboard',
    label: '仪表盘',
    icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
  },
  { path: '/traces', label: '轨迹', icon: 'M13 10V3L4 14h7v7l9-11h-7z' },
  {
    path: '/evolution',
    label: '自进化',
    adminOnly: true,
    icon: 'M4 4v6h6M20 20v-6h-6M5 19a9 9 0 0014-7M19 5A9 9 0 005 12',
  },
]

const sessions = ref<SessionItem[]>([])
const loading = ref(false)
const pendingCount = ref(0)
const newSessionId = ref<string | null>(null) // 追踪最新创建的会话 ID，用于 loadSessions 后重新标记

async function loadPendingCount() {
  if (!auth.isAuthenticated) {
    pendingCount.value = 0
    return
  }
  try {
    const data = await getPersonalInfo()
    pendingCount.value = (data.pending_items || []).length
  } catch {
    pendingCount.value = 0
  }
}

async function loadSessions() {
  if (loading.value || !auth.isAuthenticated) return
  loading.value = true
  try {
    sessions.value = await getSessions(200, 0)
    // 重新标记新建会话（loadSessions 会丢失 _isNew 标记）
    if (newSessionId.value) {
      const s = sessions.value.find((s) => s.session_id === newSessionId.value)
      if (s) (s as any)._isNew = true
    }
  } catch (e) {
    console.error('Failed to load sessions:', e)
  } finally {
    loading.value = false
  }
}

function openSession(session: SessionItem) {
  chatStore.sessionTitle = session.first_question || '未命名会话'
  router.push(`/chat/${session.session_id}`)
}

async function handleDelete(sessionId: string, e: Event) {
  e.stopPropagation()
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter((s) => s.session_id !== sessionId)
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
  if (auth.isAuthenticated) {
    loadSessions()
    loadPendingCount()
  }
})

watch(
  () => auth.user?.user_id,
  (userId) => {
    sessions.value = []
    pendingCount.value = 0
    if (userId) {
      loadSessions()
      loadPendingCount()
    }
  },
)

// 新会话创建时（sessionId 从 null 变为值）立即在列表头部插入占位条目
watch(
  () => chatStore.sessionId,
  (newId, oldId) => {
    if (newId && !oldId) {
      const firstQ = chatStore.messages.find((m) => m.role === 'user')?.content || '新会话'
      const placeholder: SessionItem = {
        session_id: newId,
        first_question: firstQ,
        created_at: new Date().toISOString(),
        message_count: 1,
        mode: '',
        total_tokens: 0,
        parallel_efficiency: 0,
        information_coverage: 0,
        redundancy: 0,
        _isNew: true,
      }
      if (!sessions.value.some((s) => s.session_id === newId)) {
        sessions.value.unshift(placeholder)
      }
      newSessionId.value = newId
      chatStore.sessionTitle = firstQ
    }
  },
)

// 对话流结束后刷新列表，确保数据库已持久化
watch(
  () => chatStore.isStreaming,
  (streaming, wasStreaming) => {
    if (wasStreaming && !streaming && chatStore.sessionId) {
      // 延迟刷新，确保后端持久化已完成
      setTimeout(() => {
        loadSessions()
        loadPendingCount()
      }, 500)
    }
  },
)

// 切换到其他会话时，清除乐观高亮并刷新列表（清除 _isNew 标记）
watch(
  () => route.params.sessionId,
  (newId, oldId) => {
    if (newId && newSessionId.value && newId !== newSessionId.value) {
      newSessionId.value = null
    }
    // 切换会话时刷新列表，用 API 数据替换带 _isNew 标记的对象
    if (newId && newId !== oldId) {
      loadSessions()
    }
  },
)
</script>

<template>
  <aside class="w-60 h-screen bg-slate-800 text-slate-200 flex flex-col shrink-0 overflow-hidden">
    <!-- Logo -->
    <div class="p-4 border-b border-slate-700">
      <div class="flex items-center gap-2">
        <div
          class="w-8 h-8 bg-blue-500 rounded-lg flex items-center justify-center text-white font-bold text-sm"
        >
          M
        </div>
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
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 4v16m8-8H4"
          />
        </svg>
        新建会话
      </button>
    </div>

    <!-- 导航 -->
    <nav class="px-3 space-y-1">
      <router-link
        v-for="item in navItems"
        v-show="!item.adminOnly || auth.isAdmin"
        :key="item.path"
        :to="item.path"
        class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg transition"
        :class="
          route.path.startsWith(item.path)
            ? 'bg-slate-700 text-white'
            : 'text-slate-300 hover:bg-slate-700/50 hover:text-white'
        "
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
      <div class="flex-1 min-h-0 overflow-y-auto pb-2 scrollbar-thin relative">
        <div
          v-for="s in sessions"
          :key="s.session_id"
          @click="openSession(s)"
          class="group flex items-center justify-between px-3 py-2 text-sm rounded-lg cursor-pointer transition"
          :class="[
            route.params.sessionId === s.session_id
              ? 'bg-slate-600 text-white'
              : 'text-slate-300 hover:bg-slate-700/50 hover:text-white',
            s._isNew ? 'bg-slate-600 text-white' : '',
          ]"
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
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </div>
        </div>
        <div
          v-if="loading"
          class="sticky bottom-0 text-center py-1.5 text-xs text-slate-500 bg-slate-800/90 backdrop-blur-sm"
        >
          加载中...
        </div>
      </div>
    </div>

    <!-- 底部：个人中心 -->
    <div class="p-3 border-t border-slate-700">
      <router-link
        to="/personal"
        class="flex items-center gap-3 px-3 py-2 text-sm rounded-lg transition relative"
        :class="
          route.path === '/personal'
            ? 'bg-slate-700 text-white'
            : 'text-slate-400 hover:bg-slate-700/50 hover:text-white'
        "
      >
        <svg class="w-5 h-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="1.5"
            d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
          />
        </svg>
        <span class="truncate">{{ auth.user?.username || '登录' }}</span>
        <span v-if="auth.isAdmin" class="text-[10px] text-blue-300">管理员</span>
        <span
          v-if="pendingCount > 0"
          class="absolute right-3 top-1/2 -translate-y-1/2 min-w-[18px] h-[18px] px-1 flex items-center justify-center text-[10px] font-medium bg-orange-500 text-white rounded-full"
        >
          {{ pendingCount }}
        </span>
      </router-link>
    </div>

    <!-- 版本信息 -->
    <div class="px-3 pb-3 text-xs text-slate-600">MediZJ Agent Swarm v0.1.0</div>
  </aside>
</template>
