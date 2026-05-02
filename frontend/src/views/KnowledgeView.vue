<script setup lang="ts">
import { ref } from 'vue'
import { searchKnowledge } from '../api/knowledge'
import type { KnowledgeItem } from '../types'

const query = ref('')
const filterType = ref<string | null>(null)
const results = ref<KnowledgeItem[]>([])
const loading = ref(false)
const searched = ref(false)

const types = [
  { key: null, label: '全部' },
  { key: 'lifestyle', label: '生活方式' },
  { key: 'symptoms', label: '症状处理' },
  { key: 'disease_classification', label: '疾病编码' },
  { key: 'clinical_guideline', label: '临床指南' },
]

async function handleSearch() {
  if (!query.value.trim()) return
  loading.value = true
  searched.value = true
  try {
    const data = await searchKnowledge({
      query: query.value,
      top_k: 10,
      filter_type: filterType.value || undefined,
    })
    results.value = data.results || []
  } catch (e) {
    console.error('Knowledge search error:', e)
    results.value = []
  } finally {
    loading.value = false
  }
}

function setFilter(key: string | null) {
  filterType.value = key
  if (searched.value) handleSearch()
}

const typeColors: Record<string, string> = {
  lifestyle: 'bg-green-100 text-green-700',
  symptoms: 'bg-red-100 text-red-700',
  disease_classification: 'bg-purple-100 text-purple-700',
  clinical_guideline: 'bg-blue-100 text-blue-700',
}
</script>

<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-4xl mx-auto">
      <!-- 搜索框 -->
      <div class="mb-6">
        <div class="flex gap-3">
          <input
            v-model="query"
            @keydown.enter="handleSearch"
            placeholder="搜索医学知识..."
            class="flex-1 px-4 py-3 border border-slate-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            @click="handleSearch"
            :disabled="loading || !query.trim()"
            class="px-6 py-3 bg-blue-500 text-white rounded-xl text-sm hover:bg-blue-600 disabled:bg-slate-300 transition"
          >
            {{ loading ? '搜索中...' : '搜索' }}
          </button>
        </div>

        <!-- 类型过滤 -->
        <div class="flex gap-2 mt-3">
          <button
            v-for="t in types"
            :key="t.key ?? 'all'"
            @click="setFilter(t.key)"
            class="px-3 py-1 text-xs rounded-full border transition"
            :class="filterType === t.key
              ? 'bg-blue-500 text-white border-blue-500'
              : 'bg-white text-slate-600 border-slate-300 hover:border-blue-300'"
          >
            {{ t.label }}
          </button>
        </div>
      </div>

      <!-- 搜索结果 -->
      <div v-if="loading" class="text-center py-12 text-slate-400">
        搜索中...
      </div>
      <div v-else-if="results.length > 0" class="space-y-4">
        <div
          v-for="item in results"
          :key="item.id"
          class="bg-white border border-slate-200 rounded-xl p-4 hover:shadow-sm transition"
        >
          <div class="flex items-center gap-2 mb-2">
            <span
              v-if="item.metadata?.type"
              class="px-2 py-0.5 text-xs rounded-full"
              :class="typeColors[item.metadata.type] || 'bg-slate-100 text-slate-600'"
            >
              {{ item.metadata.type }}
            </span>
            <span v-if="item.score" class="text-xs text-slate-400">
              相关度: {{ (item.score * 100).toFixed(0) }}%
            </span>
          </div>
          <p class="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">{{ item.content }}</p>
          <div v-if="item.metadata?.source" class="mt-2 text-xs text-slate-400">
            来源: {{ item.metadata.source }}
          </div>
        </div>
      </div>
      <div v-else-if="searched" class="text-center py-12 text-slate-400">
        未找到相关结果
      </div>
      <div v-else class="text-center py-12 text-slate-400">
        <p>输入关键词搜索医学知识库</p>
        <p class="text-xs mt-2">支持疾病、症状、治疗方案等搜索</p>
      </div>
    </div>
  </div>
</template>
