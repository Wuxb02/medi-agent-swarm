<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getSessions, deleteSession } from '../api/session'
import type { SessionItem } from '../types'

const router = useRouter()
const sessions = ref<SessionItem[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    sessions.value = await getSessions()
  } catch (e) {
    console.error('Failed to load sessions:', e)
  } finally {
    loading.value = false
  }
})

function openSession(sessionId: string) {
  router.push(`/chat/${sessionId}`)
}

async function handleDelete(sessionId: string, e: Event) {
  e.stopPropagation()
  if (!confirm('确定删除该会话？')) return
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter((s) => s.session_id !== sessionId)
  } catch (e) {
    console.error('Delete failed:', e)
  }
}

function formatDate(dateStr: string) {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleString('zh-CN')
  } catch {
    return dateStr
  }
}
</script>

<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-4xl mx-auto">
      <h2 class="text-lg font-semibold text-slate-800 mb-4">历史会话</h2>

      <div v-if="loading" class="text-center py-12 text-slate-400">加载中...</div>
      <div v-else-if="sessions.length === 0" class="text-center py-12 text-slate-400">
        暂无历史会话
      </div>
      <div v-else class="space-y-3">
        <div
          v-for="s in sessions"
          :key="s.session_id"
          @click="openSession(s.session_id)"
          class="bg-white border border-slate-200 rounded-xl p-4 hover:shadow-sm hover:border-blue-200 transition cursor-pointer group"
        >
          <div class="flex items-start justify-between">
            <div class="flex-1 min-w-0">
              <p class="text-sm text-slate-800 font-medium truncate">
                {{ s.first_question || '未命名会话' }}
              </p>
              <div class="flex items-center gap-3 mt-2 text-xs text-slate-400">
                <span>{{ formatDate(s.created_at) }}</span>
                <span
                  class="px-1.5 py-0.5 rounded text-xs"
                  :class="
                    s.mode === 'swarm'
                      ? 'bg-green-50 text-green-600'
                      : 'bg-slate-100 text-slate-500'
                  "
                >
                  {{ s.mode === 'swarm' ? 'Swarm' : '单 Agent' }}
                </span>
                <span v-if="s.message_count">{{ s.message_count }} 条消息</span>
                <span v-if="s.total_tokens" class="text-amber-500">
                  {{ s.total_tokens.toLocaleString() }} tokens
                </span>
              </div>
            </div>
            <button
              @click="handleDelete(s.session_id, $event)"
              class="p-1.5 text-slate-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
