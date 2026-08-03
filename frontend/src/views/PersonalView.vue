<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import {
  getPersonalInfo,
  updatePersonalInfo,
  confirmPending,
  dismissPending,
  updateMedicalRecords,
} from '../api/personal'
import type { PersonalInfoItem, PendingItem, MedicalRecord } from '../api/personal'

const auth = useAuthStore()
const chatStore = useChatStore()
const route = useRoute()
const router = useRouter()
const username = ref('')

async function handleLogin() {
  if (!username.value.trim()) return
  try {
    await auth.login(username.value)
    await loadInfo()
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/chat'
    await router.replace(redirect)
  } catch {
    // 错误信息由认证 Store 展示
  }
}

async function handleLogout() {
  await auth.logout()
  chatStore.clearChat()
  items.value = []
  pendingItems.value = []
  medicalRecords.value = []
  await router.replace('/personal')
}

// ========== 数据状态 ==========
const items = ref<PersonalInfoItem[]>([])
const pendingItems = ref<PendingItem[]>([])
const medicalRecords = ref<MedicalRecord[]>([])
const loading = ref(false)
const saving = ref(false)
const saved = ref(false)
const error = ref<string | null>(null)

// 编辑模式
const editing = ref(false)
const newKey = ref('')
const newValue = ref('')

// 病史编辑模式
const editingRecords = ref(false)
const newRecordDate = ref('')
const newRecordDesc = ref('')
const newRecordSymptoms = ref('')
const newRecordDuration = ref('')
const newRecordMedication = ref('')
const newRecordOutcome = ref('')

const pendingCount = computed(() => pendingItems.value.length)

// ========== 加载数据 ==========
async function loadInfo() {
  loading.value = true
  error.value = null
  try {
    const data = await getPersonalInfo()
    items.value = data.items || []
    pendingItems.value = data.pending_items || []
    medicalRecords.value = data.medical_records || []
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载失败'
    items.value = []
    pendingItems.value = []
    medicalRecords.value = []
  } finally {
    loading.value = false
  }
}

// ========== 个人信息编辑 ==========
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

// ========== 待确认操作 ==========
async function handleConfirm(item: PendingItem) {
  try {
    await confirmPending(item.key, item.value)
    await loadInfo()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '确认失败'
  }
}

async function handleDismiss(item: PendingItem) {
  try {
    await dismissPending(item.key, item.value)
    await loadInfo()
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '丢弃失败'
  }
}

// ========== 病史编辑 ==========
function addRecord() {
  if (!newRecordDate.value.trim() || !newRecordDesc.value.trim()) return
  medicalRecords.value.push({
    date: newRecordDate.value.trim(),
    description: newRecordDesc.value.trim(),
    symptoms: newRecordSymptoms.value.trim(),
    duration: newRecordDuration.value.trim(),
    medication: newRecordMedication.value.trim(),
    outcome: newRecordOutcome.value.trim(),
  })
  newRecordDate.value = ''
  newRecordDesc.value = ''
  newRecordSymptoms.value = ''
  newRecordDuration.value = ''
  newRecordMedication.value = ''
  newRecordOutcome.value = ''
}

function removeRecord(index: number) {
  medicalRecords.value.splice(index, 1)
}

function startEditRecords() {
  editingRecords.value = true
}

function cancelEditRecords() {
  editingRecords.value = false
  loadInfo()
}

async function saveRecords() {
  saving.value = true
  error.value = null
  try {
    await updateMedicalRecords(medicalRecords.value)
    editingRecords.value = false
    saved.value = true
    setTimeout(() => (saved.value = false), 2000)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

function formatDate(dateStr: string): string {
  return dateStr
}

function confidenceLabel(c: string): string {
  return c === 'high' ? '高' : '中'
}

function confidenceClass(c: string): string {
  return c === 'high' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
}

onMounted(async () => {
  await auth.restore()
  if (auth.isAuthenticated) await loadInfo()
})
</script>

<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-2xl mx-auto space-y-8">
      <section
        v-if="!auth.isAuthenticated"
        class="max-w-md mx-auto mt-20 bg-white border border-slate-200 rounded-2xl p-8 shadow-sm"
      >
        <div class="text-center mb-6">
          <div
            class="w-12 h-12 mx-auto mb-3 rounded-xl bg-blue-500 text-white flex items-center justify-center font-bold"
          >
            M
          </div>
          <h1 class="text-xl font-semibold text-slate-800">登录 MediZJ</h1>
          <p class="mt-2 text-sm text-slate-500">输入用户名即可登录，首次使用会自动创建账号</p>
        </div>
        <form class="space-y-4" @submit.prevent="handleLogin">
          <input
            v-model="username"
            autocomplete="username"
            autofocus
            maxlength="64"
            pattern="[A-Za-z0-9_-]+"
            placeholder="用户名"
            class="w-full px-3 py-2.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <div v-if="auth.error" class="text-sm text-red-600">{{ auth.error }}</div>
          <button
            type="submit"
            :disabled="auth.loading || !username.trim()"
            class="w-full py-2.5 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:bg-slate-300 transition"
          >
            {{ auth.loading ? '登录中...' : '登录' }}
          </button>
        </form>
        <p class="mt-5 text-xs leading-relaxed text-amber-600 bg-amber-50 rounded-lg p-3">
          当前为免密登录，仅适用于本地或可信网络环境，请勿用于公开生产环境。
        </p>
      </section>

      <template v-else>
        <section
          class="flex items-center justify-between bg-white border border-slate-200 rounded-xl p-4"
        >
          <div>
            <div class="text-sm font-medium text-slate-800">{{ auth.user?.username }}</div>
            <div class="text-xs text-slate-500 mt-0.5">
              {{ auth.isAdmin ? '管理员' : '普通用户' }}
            </div>
          </div>
          <button
            @click="handleLogout"
            class="px-3 py-1.5 text-sm text-slate-600 bg-slate-100 rounded-lg hover:bg-slate-200 transition"
          >
            退出登录
          </button>
        </section>
        <!-- 错误提示 -->
        <div
          v-if="error"
          class="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700"
        >
          {{ error }}
        </div>
        <!-- 保存成功 -->
        <div
          v-if="saved"
          class="p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700"
        >
          保存成功
        </div>

        <!-- 加载中 -->
        <div v-if="loading" class="text-center py-16 text-slate-400 text-sm">加载中...</div>

        <template v-else>
          <!-- ========== 区域 1：个人信息 ========== -->
          <section>
            <div class="flex items-center justify-between mb-4">
              <div>
                <h2 class="text-lg font-semibold text-slate-800">个人信息</h2>
                <p class="text-sm text-slate-500 mt-1">
                  已确认的健康档案（年龄、过敏史、慢性病等）
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

            <!-- 查看模式 -->
            <div v-if="!editing">
              <div v-if="items.length === 0" class="text-center py-10 text-slate-400">
                <p class="text-sm">暂无个人信息</p>
                <p class="text-xs mt-1">点击"编辑"手动添加，或在对话中自动提取后到待确认区确认</p>
              </div>
              <div
                v-else
                class="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100"
              >
                <div v-for="item in items" :key="item.key" class="flex items-center px-5 py-3.5">
                  <span class="w-24 text-sm font-medium text-slate-600 shrink-0">{{
                    item.key
                  }}</span>
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
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </div>
              <div
                class="flex items-center gap-3 bg-slate-50 border border-dashed border-slate-300 rounded-lg px-4 py-3"
              >
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
                    <path
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      stroke-width="2"
                      d="M12 4v16m8-8H4"
                    />
                  </svg>
                </button>
              </div>
            </div>
          </section>

          <!-- ========== 区域 2：待确认信息 ========== -->
          <section v-if="pendingCount > 0">
            <div class="flex items-center gap-2 mb-4">
              <h2 class="text-lg font-semibold text-slate-800">待确认信息</h2>
              <span
                class="px-2 py-0.5 text-xs font-medium bg-orange-100 text-orange-700 rounded-full"
              >
                {{ pendingCount }} 条
              </span>
            </div>
            <p class="text-sm text-slate-500 mb-3">以下信息从对话中自动提取，请确认后保存</p>
            <div class="space-y-2">
              <div
                v-for="item in pendingItems"
                :key="`${item.key}-${item.value}`"
                class="bg-white border border-slate-200 rounded-lg px-4 py-3"
              >
                <!-- 病史类型 -->
                <div v-if="item.is_record" class="flex items-start justify-between">
                  <div class="flex-1">
                    <div class="flex items-center gap-2">
                      <span class="px-1.5 py-0.5 text-xs rounded bg-blue-50 text-blue-700"
                        >病史</span
                      >
                      <span class="text-sm font-medium text-slate-800">{{ item.value }}</span>
                      <span v-if="item.record_date" class="text-xs text-slate-500">{{
                        item.record_date
                      }}</span>
                    </div>
                    <div class="mt-1 space-y-0.5">
                      <p v-if="item.symptoms" class="text-xs text-slate-600">
                        症状：{{ item.symptoms }}
                      </p>
                      <p v-if="item.duration" class="text-xs text-slate-600">
                        持续：{{ item.duration }}
                      </p>
                      <p v-if="item.medication" class="text-xs text-slate-600">
                        用药：{{ item.medication }}
                      </p>
                    </div>
                    <span class="text-xs text-slate-400">{{ item.source_date }} 提取</span>
                  </div>
                  <div class="flex gap-2 ml-3 shrink-0">
                    <button
                      @click="handleConfirm(item)"
                      class="px-3 py-1.5 text-xs bg-green-500 text-white rounded-lg hover:bg-green-600 transition"
                    >
                      确认
                    </button>
                    <button
                      @click="handleDismiss(item)"
                      class="px-3 py-1.5 text-xs bg-slate-200 text-slate-600 rounded-lg hover:bg-slate-300 transition"
                    >
                      丢弃
                    </button>
                  </div>
                </div>
                <!-- 信息类型 -->
                <div v-else class="flex items-center justify-between">
                  <div class="flex-1">
                    <div class="flex items-center gap-2">
                      <span class="text-sm font-medium text-slate-600">{{ item.key }}：</span>
                      <span class="text-sm text-slate-800">{{ item.value }}</span>
                      <span
                        :class="['px-1.5 py-0.5 text-xs rounded', confidenceClass(item.confidence)]"
                      >
                        置信度：{{ confidenceLabel(item.confidence) }}
                      </span>
                    </div>
                    <span class="text-xs text-slate-400">{{ item.source_date }} 提取</span>
                  </div>
                  <div class="flex gap-2 ml-3">
                    <button
                      @click="handleConfirm(item)"
                      class="px-3 py-1.5 text-xs bg-green-500 text-white rounded-lg hover:bg-green-600 transition"
                    >
                      确认
                    </button>
                    <button
                      @click="handleDismiss(item)"
                      class="px-3 py-1.5 text-xs bg-slate-200 text-slate-600 rounded-lg hover:bg-slate-300 transition"
                    >
                      丢弃
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <!-- ========== 区域 3：病史记录 ========== -->
          <section>
            <div class="flex items-center justify-between mb-4">
              <div>
                <h2 class="text-lg font-semibold text-slate-800">病史记录</h2>
                <p class="text-sm text-slate-500 mt-1">患病经历时间线（对话中自动记录）</p>
              </div>
              <div class="flex gap-2">
                <button
                  v-if="!editingRecords"
                  @click="startEditRecords"
                  class="px-4 py-2 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition"
                >
                  编辑
                </button>
                <template v-if="editingRecords">
                  <button
                    @click="saveRecords"
                    :disabled="saving"
                    class="px-4 py-2 text-sm bg-green-500 text-white rounded-lg hover:bg-green-600 disabled:bg-slate-300 transition"
                  >
                    {{ saving ? '保存中...' : '保存' }}
                  </button>
                  <button
                    @click="cancelEditRecords"
                    class="px-4 py-2 text-sm bg-slate-200 text-slate-600 rounded-lg hover:bg-slate-300 transition"
                  >
                    取消
                  </button>
                </template>
              </div>
            </div>

            <!-- 查看模式 -->
            <div v-if="!editingRecords">
              <div v-if="medicalRecords.length === 0" class="text-center py-10 text-slate-400">
                <p class="text-sm">暂无病史记录</p>
                <p class="text-xs mt-1">在对话中描述患病经历时会自动记录</p>
              </div>
              <div v-else class="space-y-3">
                <div
                  v-for="record in medicalRecords"
                  :key="`${record.date}-${record.description}`"
                  class="bg-white border border-slate-200 rounded-xl p-4"
                >
                  <div class="flex items-start gap-3">
                    <span
                      class="shrink-0 px-2 py-1 text-xs font-medium bg-blue-50 text-blue-700 rounded"
                    >
                      {{ formatDate(record.date) }}
                    </span>
                    <div class="flex-1 min-w-0">
                      <p class="text-sm font-medium text-slate-800">{{ record.description }}</p>
                      <div class="mt-1 space-y-0.5">
                        <p v-if="record.symptoms" class="text-xs text-slate-600">
                          症状：{{ record.symptoms }}
                        </p>
                        <p v-if="record.duration" class="text-xs text-slate-600">
                          持续：{{ record.duration }}
                        </p>
                        <p v-if="record.medication" class="text-xs text-slate-600">
                          用药：{{ record.medication }}
                        </p>
                        <p v-if="record.outcome" class="text-xs text-slate-600">
                          转归：{{ record.outcome }}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 编辑模式 -->
            <div v-else class="space-y-3">
              <div
                v-for="(record, index) in medicalRecords"
                :key="index"
                class="bg-white border border-slate-200 rounded-lg p-4 space-y-2"
              >
                <div class="flex items-center gap-2">
                  <input
                    v-model="record.date"
                    placeholder="年月（如：2025-05）"
                    class="w-28 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <input
                    v-model="record.description"
                    placeholder="病名（如：感冒）"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <button
                    @click="removeRecord(index)"
                    class="p-1.5 text-slate-400 hover:text-red-500 transition"
                    title="删除"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M6 18L18 6M6 6l12 12"
                      />
                    </svg>
                  </button>
                </div>
                <div class="flex items-center gap-2">
                  <input
                    v-model="record.symptoms"
                    placeholder="症状"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <input
                    v-model="record.medication"
                    placeholder="用药"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div class="flex items-center gap-2">
                  <input
                    v-model="record.duration"
                    placeholder="持续时间"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <select
                    v-model="record.outcome"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">转归状态</option>
                    <option value="已康复">已康复</option>
                    <option value="好转中">好转中</option>
                    <option value="未愈">未愈</option>
                    <option value="恶化">恶化</option>
                  </select>
                </div>
              </div>

              <!-- 新增病史 -->
              <div
                class="bg-slate-50 border border-dashed border-slate-300 rounded-lg p-4 space-y-2"
              >
                <div class="flex items-center gap-2">
                  <input
                    v-model="newRecordDate"
                    placeholder="年月（如：2025-05）"
                    class="w-28 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <input
                    v-model="newRecordDesc"
                    placeholder="病名（如：感冒）"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                    @keydown.enter="addRecord"
                  />
                  <button
                    @click="addRecord"
                    :disabled="!newRecordDate.trim() || !newRecordDesc.trim()"
                    class="p-1.5 text-blue-500 hover:text-blue-700 disabled:text-slate-300 transition"
                    title="添加"
                  >
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M12 4v16m8-8H4"
                      />
                    </svg>
                  </button>
                </div>
                <div class="flex items-center gap-2">
                  <input
                    v-model="newRecordSymptoms"
                    placeholder="症状"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <input
                    v-model="newRecordMedication"
                    placeholder="用药"
                    class="flex-1 px-2 py-1.5 text-sm border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>
            </div>
          </section>
        </template>
      </template>
    </div>
  </div>
</template>
