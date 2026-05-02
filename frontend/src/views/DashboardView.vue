<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getDashboardStats } from '../api/dashboard'
import type { DashboardStats } from '../types'

const stats = ref<DashboardStats | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

async function loadDashboard() {
  loading.value = true
  error.value = null
  try {
    stats.value = await getDashboardStats()
  } catch (e: any) {
    error.value = e.message || '加载仪表盘数据失败'
    console.error('Failed to load dashboard:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadDashboard)

const statCards = [
  { key: 'total_sessions', label: '总会话数', icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z', color: 'blue' },
  { key: 'swarm_sessions', label: 'Swarm 协作', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z', color: 'green' },
  { key: 'knowledge_base_size', label: '知识库文档', icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253', color: 'purple' },
  { key: 'total_messages', label: '总消息数', icon: 'M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z', color: 'amber' },
]

const colorMap: Record<string, string> = {
  blue: 'bg-blue-50 text-blue-500',
  green: 'bg-green-50 text-green-500',
  purple: 'bg-purple-50 text-purple-500',
  amber: 'bg-amber-50 text-amber-500',
}

function getStatValue(key: string): number {
  if (!stats.value) return 0
  const record = stats.value as unknown as Record<string, unknown>
  const val = record[key]
  return typeof val === 'number' ? val : 0
}

function getAgentBarWidth(count: unknown): number {
  if (!stats.value) return 0
  const values = Object.values(stats.value.agents_usage)
  const maxVal = Math.max(...values, 1)
  return Math.min(100, (Number(count) / maxVal) * 100)
}
</script>

<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-5xl mx-auto">
      <div v-if="loading" class="text-center py-12 text-slate-400">加载中...</div>
      <div v-else-if="error" class="text-center py-12">
        <p class="text-red-500 mb-3">{{ error }}</p>
        <button @click="loadDashboard()" class="text-sm text-blue-500 hover:underline">重试</button>
      </div>
      <template v-else-if="stats">
        <!-- 统计卡片 -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div
            v-for="card in statCards"
            :key="card.key"
            class="bg-white border border-slate-200 rounded-xl p-4"
          >
            <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-lg flex items-center justify-center" :class="colorMap[card.color]">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" :d="card.icon" />
                </svg>
              </div>
              <div>
                <div class="text-2xl font-bold text-slate-800">{{ getStatValue(card.key) }}</div>
                <div class="text-xs text-slate-500">{{ card.label }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Agent 使用分布 -->
        <div class="bg-white border border-slate-200 rounded-xl p-5 mb-6">
          <h3 class="text-sm font-semibold text-slate-700 mb-4">Agent 使用分布</h3>
          <div class="space-y-3">
            <div v-for="(count, agent) in stats.agents_usage" :key="agent" class="flex items-center gap-3">
              <span class="text-xs text-slate-600 w-36 truncate">{{ agent }}</span>
              <div class="flex-1 bg-slate-100 rounded-full h-3 overflow-hidden">
                <div
                  class="h-full bg-blue-500 rounded-full transition-all"
                  :style="{ width: `${getAgentBarWidth(count)}%` }"
                />
              </div>
              <span class="text-xs text-slate-500 w-8 text-right">{{ count }}</span>
            </div>
          </div>
        </div>

        <!-- 最近会话 -->
        <div class="bg-white border border-slate-200 rounded-xl p-5">
          <h3 class="text-sm font-semibold text-slate-700 mb-4">最近会话</h3>
          <div v-if="stats.recent_sessions.length === 0" class="text-sm text-slate-400 text-center py-4">
            暂无会话记录
          </div>
          <div v-else class="space-y-2">
            <div
              v-for="s in stats.recent_sessions"
              :key="s.session_id"
              class="flex items-center gap-3 py-2 px-3 rounded-lg hover:bg-slate-50 text-sm"
            >
              <span class="w-1.5 h-1.5 rounded-full shrink-0"
                :class="s.mode === 'swarm' ? 'bg-green-400' : 'bg-slate-300'"
              />
              <span class="flex-1 truncate text-slate-700">{{ s.first_question || '未命名' }}</span>
              <span class="text-xs text-slate-400 shrink-0">{{ s.created_at ? new Date(s.created_at).toLocaleDateString('zh-CN') : '' }}</span>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
