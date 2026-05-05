<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getPersonalInfo, updatePersonalInfo } from '../api/personal'
import type { PersonalInfoItem } from '../api/personal'

const items = ref<PersonalInfoItem[]>([])
const loading = ref(false)
const saving = ref(false)
const saved = ref(false)
const error = ref<string | null>(null)
const editing = ref(false)

// 新增行的临时状态
const newKey = ref('')
const newValue = ref('')

async function loadInfo() {
  loading.value = true
  error.value = null
  try {
    const data = await getPersonalInfo()
    items.value = data.items || []
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

async function saveInfo() {
  saving.value = true
  error.value = null
  saved.value = false
  try {
    const data = await updatePersonalInfo(items.value)
    items.value = data.items || []
    editing.value = false
    saved.value = true
    setTimeout(() => (saved.value = false), 2000)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

function addItem() {
  if (!newKey.value.trim()) return
  items.value.push({ key: newKey.value.trim(), value: newValue.value.trim() })
  newKey.value = ''
  newValue.value = ''
}

function removeItem(index: number) {
  items.value.splice(index, 1)
}

function startEdit() {
  editing.value = true
  saved.value = false
}

function cancelEdit() {
  editing.value = false
  loadInfo()
}

onMounted(loadInfo)
</script>

<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-2xl mx-auto">
      <div class="flex items-center justify-between mb-6">
        <div>
          <h2 class="text-lg font-semibold text-slate-800">个人中心</h2>
          <p class="text-sm text-slate-500 mt-1">
            管理个人健康档案（年龄、性别、病史等）
          </p>
        </div>
        <div class="flex gap-2">
          <button
            v-if="!editing"
            @click="startEdit"
            class="px-4 py-2 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition"
          >
            编辑
          </button>
          <template v-if="editing">
            <button
              @click="saveInfo"
              :disabled="saving"
              class="px-4 py-2 text-sm bg-green-500 text-white rounded-lg hover:bg-green-600 disabled:bg-slate-300 transition"
            >
              {{ saving ? '保存中...' : '保存' }}
            </button>
            <button
              @click="cancelEdit"
              class="px-4 py-2 text-sm bg-slate-200 text-slate-600 rounded-lg hover:bg-slate-300 transition"
            >
              取消
            </button>
          </template>
        </div>
      </div>

      <!-- 提示 -->
      <div
        v-if="saved"
        class="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700"
      >
        保存成功
      </div>
      <div
        v-if="error"
        class="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700"
      >
        {{ error }}
      </div>

      <!-- 加载中 -->
      <div v-if="loading" class="text-center py-16 text-slate-400 text-sm">
        加载中...
      </div>

      <!-- 查看模式 -->
      <div v-else-if="!editing">
        <div v-if="items.length === 0" class="text-center py-16 text-slate-400">
          <svg class="w-12 h-12 mx-auto mb-3 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          <p class="text-sm">暂无个人信息</p>
          <p class="text-xs mt-1">点击"编辑"手动添加，或在对话中自动提取</p>
        </div>
        <div v-else class="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100">
          <div
            v-for="item in items"
            :key="item.key"
            class="flex items-center px-5 py-3.5"
          >
            <span class="w-24 text-sm font-medium text-slate-600 shrink-0">{{ item.key }}</span>
            <span class="text-sm text-slate-800">{{ item.value }}</span>
          </div>
        </div>
      </div>

      <!-- 编辑模式 -->
      <div v-else class="space-y-3">
        <div
          v-for="(item, index) in items"
          :key="index"
          class="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-3"
        >
          <input
            v-model="item.key"
            placeholder="字段名（如：年龄）"
            class="w-28 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <input
            v-model="item.value"
            placeholder="值（如：28岁）"
            class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            @click="removeItem(index)"
            class="p-1.5 text-slate-400 hover:text-red-500 transition"
            title="删除"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <!-- 新增行 -->
        <div class="flex items-center gap-3 bg-slate-50 border border-dashed border-slate-300 rounded-lg px-4 py-3">
          <input
            v-model="newKey"
            placeholder="字段名（如：过敏史）"
            class="w-28 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
            @keydown.enter="addItem"
          />
          <input
            v-model="newValue"
            placeholder="值（如：青霉素过敏）"
            class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
            @keydown.enter="addItem"
          />
          <button
            @click="addItem"
            :disabled="!newKey.trim()"
            class="p-1.5 text-blue-500 hover:text-blue-700 disabled:text-slate-300 transition"
            title="添加"
          >
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
            </svg>
          </button>
        </div>

        <p class="text-xs text-slate-400 mt-2">
          提示：系统会在对话中自动提取个人信息，您也可以在此手动维护。
        </p>
      </div>
    </div>
  </div>
</template>
