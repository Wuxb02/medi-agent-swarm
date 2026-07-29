<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  searchKnowledge,
  getDocuments,
  getDocumentChunks,
  deleteDocument,
  uploadDocument,
  updateDocument,
} from '../api/knowledge'
import type { KnowledgeItem, DocumentSummary, ChunkDetail } from '../types'

// Tab 控制
type TabKey = 'search' | 'documents' | 'upload'
const activeTab = ref<TabKey>('search')

// ========== 搜索 Tab ==========
const query = ref('')
const filterType = ref<string | null>(null)
const results = ref<KnowledgeItem[]>([])
const searchLoading = ref(false)
const searched = ref(false)

const filterTypes = [
  { key: null, label: '全部' },
  { key: 'lifestyle', label: '生活方式' },
  { key: 'symptoms', label: '症状处理' },
  { key: 'disease_classification', label: '疾病编码' },
  { key: 'clinical_guideline', label: '临床指南' },
]

async function handleSearch() {
  if (!query.value.trim()) return
  searchLoading.value = true
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
    searchLoading.value = false
  }
}

function setFilter(key: string | null) {
  filterType.value = key
  if (searched.value) handleSearch()
}

const expandedItems = ref<Set<string>>(new Set())

function toggleExpand(id: string) {
  if (expandedItems.value.has(id)) {
    expandedItems.value.delete(id)
  } else {
    expandedItems.value.add(id)
  }
}

// ========== 文档管理 Tab ==========
const documents = ref<DocumentSummary[]>([])
const docLoading = ref(false)
const selectedDocId = ref<string | null>(null)
const chunks = ref<ChunkDetail[]>([])
const chunkLoading = ref(false)
const confirmDeleteId = ref<string | null>(null)

// 编辑模式
const editing = ref(false)
const editContent = ref('')
const editSaving = ref(false)

async function loadDocuments() {
  docLoading.value = true
  try {
    const data = await getDocuments()
    documents.value = data.documents || []
  } catch (e) {
    console.error('Load documents error:', e)
    documents.value = []
  } finally {
    docLoading.value = false
  }
}

async function viewChunks(docId: string) {
  selectedDocId.value = docId
  editing.value = false
  chunkLoading.value = true
  try {
    const data = await getDocumentChunks(docId)
    chunks.value = data.chunks || []
  } catch (e) {
    console.error('Load chunks error:', e)
    chunks.value = []
  } finally {
    chunkLoading.value = false
  }
}

function closeChunkPanel() {
  selectedDocId.value = null
  chunks.value = []
  editing.value = false
}

async function handleDelete(docId: string) {
  try {
    await deleteDocument(docId)
    confirmDeleteId.value = null
    if (selectedDocId.value === docId) closeChunkPanel()
    await loadDocuments()
  } catch (e) {
    console.error('Delete error:', e)
  }
}

function startEdit() {
  editContent.value = chunks.value
    .slice()
    .sort((a, b) => a.chunk_id - b.chunk_id)
    .map((c) => c.content)
    .join('\n\n')
  editing.value = true
}

async function saveEdit() {
  if (!selectedDocId.value || !editContent.value.trim()) return
  editSaving.value = true
  try {
    await updateDocument(selectedDocId.value, editContent.value)
    editing.value = false
    await viewChunks(selectedDocId.value)
    await loadDocuments()
  } catch (e) {
    console.error('Save error:', e)
  } finally {
    editSaving.value = false
  }
}

function copyChunk(content: string) {
  navigator.clipboard.writeText(content)
}

// 监听 tab 切换，加载文档列表
watch(activeTab, (tab) => {
  if (tab === 'documents') loadDocuments()
})

// ========== 上传 Tab ==========
const uploadFile = ref<File | null>(null)
const uploadDocType = ref('general')
const uploadDisease = ref('')
const uploadSource = ref('用户上传')
const uploading = ref(false)
const uploadResult = ref<string | null>(null)
const uploadError = ref<string | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)

const docTypeOptions = [
  { value: 'general', label: '通用' },
  { value: 'lifestyle', label: '生活方式' },
  { value: 'symptoms', label: '症状处理' },
  { value: 'disease_classification', label: '疾病编码' },
  { value: 'clinical_guideline', label: '临床指南' },
]

function onFileSelect(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files[0]) {
    uploadFile.value = input.files[0]
    uploadResult.value = null
    uploadError.value = null
  }
}

function onDrop(e: DragEvent) {
  dragOver.value = false
  if (e.dataTransfer?.files && e.dataTransfer.files[0]) {
    uploadFile.value = e.dataTransfer.files[0]
    uploadResult.value = null
    uploadError.value = null
  }
}

function triggerFileInput() {
  fileInputRef.value?.click()
}

async function handleUpload() {
  if (!uploadFile.value) return
  uploading.value = true
  uploadResult.value = null
  uploadError.value = null
  try {
    const data = await uploadDocument(
      uploadFile.value,
      uploadDocType.value,
      uploadDisease.value,
      uploadSource.value,
    )
    uploadResult.value = `上传成功：${data.filename}，生成 ${data.chunks_added} 个分块`
    uploadFile.value = null
    if (fileInputRef.value) fileInputRef.value.value = ''
    uploadDisease.value = ''
  } catch (e: any) {
    uploadError.value = e?.response?.data?.detail || '上传失败'
  } finally {
    uploading.value = false
  }
}

// 通用
const typeColors: Record<string, string> = {
  lifestyle: 'bg-green-100 text-green-700',
  symptoms: 'bg-red-100 text-red-700',
  disease_classification: 'bg-purple-100 text-purple-700',
  clinical_guideline: 'bg-blue-100 text-blue-700',
  general: 'bg-slate-100 text-slate-600',
}

const typeLabels: Record<string, string> = {
  lifestyle: '生活方式',
  symptoms: '症状处理',
  disease_classification: '疾病编码',
  clinical_guideline: '临床指南',
  general: '通用',
}

function getTypeLabel(type: string): string {
  return typeLabels[type] || type
}

const diseaseLabels: Record<string, string> = {
  hypertension: '高血压',
  diabetes: '糖尿病',
  cold: '感冒',
  general_health: '健康常识',
  emergency: '急症处理',
  cardiovascular: '心血管疾病',
  endocrine: '内分泌疾病',
  infectious: '传染病',
}

function getDiseaseLabel(disease: string): string {
  return diseaseLabels[disease] || disease
}

const selectedDoc = computed(() => documents.value.find((d) => d.doc_id === selectedDocId.value))

const tabs = [
  { key: 'search' as TabKey, label: '搜索' },
  { key: 'documents' as TabKey, label: '文档管理' },
  { key: 'upload' as TabKey, label: '上传文件' },
]
</script>

<template>
  <div class="h-full overflow-y-auto p-6">
    <div class="max-w-5xl mx-auto">
      <!-- Tab 导航 -->
      <div class="flex gap-1 mb-6 border-b border-slate-200">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          @click="activeTab = tab.key"
          class="px-4 py-2.5 text-sm font-medium transition border-b-2 -mb-px"
          :class="
            activeTab === tab.key
              ? 'border-blue-500 text-blue-600'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          "
        >
          {{ tab.label }}
        </button>
      </div>

      <!-- ====== 搜索 Tab ====== -->
      <div v-if="activeTab === 'search'">
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
              :disabled="searchLoading || !query.trim()"
              class="px-6 py-3 bg-blue-500 text-white rounded-xl text-sm hover:bg-blue-600 disabled:bg-slate-300 transition"
            >
              {{ searchLoading ? '搜索中...' : '搜索' }}
            </button>
          </div>
          <div class="flex gap-2 mt-3">
            <button
              v-for="t in filterTypes"
              :key="t.key ?? 'all'"
              @click="setFilter(t.key)"
              class="px-3 py-1 text-xs rounded-full border transition"
              :class="
                filterType === t.key
                  ? 'bg-blue-500 text-white border-blue-500'
                  : 'bg-white text-slate-600 border-slate-300 hover:border-blue-300'
              "
            >
              {{ t.label }}
            </button>
          </div>
        </div>

        <div v-if="searchLoading" class="text-center py-12 text-slate-400">搜索中...</div>
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
                {{ getTypeLabel(item.metadata.type) }}
              </span>
              <span v-if="item.score" class="text-xs text-slate-400">
                相关度: {{ (item.score * 100).toFixed(0) }}%
              </span>
            </div>
            <p
              class="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap cursor-pointer"
              :class="{ 'line-clamp-3': !expandedItems.has(item.id) }"
              @click="toggleExpand(item.id)"
            >
              {{ item.content }}
            </p>
            <button
              v-if="item.content.length > 150"
              @click="toggleExpand(item.id)"
              class="mt-1 text-xs text-blue-500 hover:text-blue-700 transition"
            >
              {{ expandedItems.has(item.id) ? '收起' : '展开全文' }}
            </button>
            <div v-if="item.metadata?.source" class="mt-2 text-xs text-slate-400">
              来源: {{ item.metadata.source }}
            </div>
          </div>
        </div>
        <div v-else-if="searched" class="text-center py-12 text-slate-400">未找到相关结果</div>
        <div v-else class="text-center py-12 text-slate-400">
          <p>输入关键词搜索医学知识库</p>
          <p class="text-xs mt-2">支持疾病、症状、治疗方案等搜索</p>
        </div>
      </div>

      <!-- ====== 文档管理 Tab ====== -->
      <div v-if="activeTab === 'documents'" class="flex gap-6">
        <!-- 文档列表 -->
        <div :class="selectedDocId ? 'w-2/5' : 'w-full'" class="transition-all">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-semibold text-slate-700">文档列表 ({{ documents.length }})</h3>
            <button
              @click="loadDocuments"
              :disabled="docLoading"
              class="text-xs text-blue-500 hover:text-blue-700 transition"
            >
              {{ docLoading ? '加载中...' : '刷新' }}
            </button>
          </div>

          <div
            v-if="docLoading && documents.length === 0"
            class="text-center py-12 text-slate-400 text-sm"
          >
            加载中...
          </div>
          <div v-else-if="documents.length === 0" class="text-center py-12 text-slate-400 text-sm">
            暂无文档
          </div>
          <div v-else class="space-y-3">
            <div
              v-for="doc in documents"
              :key="doc.doc_id"
              class="bg-white border rounded-lg p-4 hover:shadow-sm transition cursor-pointer"
              :class="
                selectedDocId === doc.doc_id
                  ? 'border-blue-400 ring-1 ring-blue-200'
                  : 'border-slate-200'
              "
              @click="viewChunks(doc.doc_id)"
            >
              <div class="flex items-start justify-between">
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 mb-1">
                    <span
                      class="px-2 py-0.5 text-xs rounded-full"
                      :class="typeColors[doc.type] || 'bg-slate-100 text-slate-600'"
                    >
                      {{ getTypeLabel(doc.type) }}
                    </span>
                    <span class="text-xs text-slate-400">{{ doc.chunk_count }} 个分块</span>
                  </div>
                  <p class="text-sm font-medium text-slate-800 truncate">{{ doc.filename }}</p>
                  <p class="text-xs text-slate-400 mt-0.5 truncate">
                    {{ getDiseaseLabel(doc.disease) }} · {{ doc.source }}
                  </p>
                </div>
                <div class="flex gap-1 ml-2 shrink-0">
                  <button
                    @click.stop="viewChunks(doc.doc_id)"
                    class="p-1.5 text-slate-400 hover:text-blue-500 hover:bg-blue-50 rounded transition"
                    title="查看详情"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                      />
                      <path
                        stroke-linecap="round"
                        stroke-linejoin="round"
                        stroke-width="2"
                        d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"
                      />
                    </svg>
                  </button>
                  <button
                    v-if="confirmDeleteId !== doc.doc_id"
                    @click.stop="confirmDeleteId = doc.doc_id"
                    class="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded transition"
                    title="删除"
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
                  <div v-else class="flex gap-1" @click.stop>
                    <button
                      @click="handleDelete(doc.doc_id)"
                      class="px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600 transition"
                    >
                      确认
                    </button>
                    <button
                      @click="confirmDeleteId = null"
                      class="px-2 py-1 text-xs bg-slate-200 text-slate-600 rounded hover:bg-slate-300 transition"
                    >
                      取消
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Chunk 详情面板 -->
        <div
          v-if="selectedDocId"
          class="w-3/5 bg-white border border-slate-200 rounded-lg overflow-hidden flex flex-col"
        >
          <div
            class="flex items-center justify-between px-4 py-3 border-b border-slate-200 bg-slate-50"
          >
            <div>
              <h4 class="text-sm font-semibold text-slate-700">{{ selectedDoc?.filename }}</h4>
              <span class="text-xs text-slate-400">{{ chunks.length }} 个分块</span>
            </div>
            <div class="flex gap-2">
              <button
                v-if="!editing"
                @click="startEdit"
                class="px-3 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition"
              >
                编辑
              </button>
              <button
                v-if="editing"
                @click="saveEdit"
                :disabled="editSaving"
                class="px-3 py-1.5 text-xs bg-green-500 text-white rounded-lg hover:bg-green-600 disabled:bg-slate-300 transition"
              >
                {{ editSaving ? '保存中...' : '保存' }}
              </button>
              <button
                v-if="editing"
                @click="editing = false"
                class="px-3 py-1.5 text-xs bg-slate-200 text-slate-600 rounded-lg hover:bg-slate-300 transition"
              >
                取消
              </button>
              <button
                @click="closeChunkPanel"
                class="p-1.5 text-slate-400 hover:text-slate-600 transition"
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
          </div>

          <div class="flex-1 overflow-y-auto p-4">
            <!-- 编辑模式 -->
            <div v-if="editing">
              <textarea
                v-model="editContent"
                class="w-full h-96 px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono resize-y"
                placeholder="文档内容..."
              ></textarea>
            </div>

            <!-- Chunk 查看模式 -->
            <div v-else-if="chunkLoading" class="text-center py-12 text-slate-400 text-sm">
              加载中...
            </div>
            <div v-else class="space-y-4">
              <div
                v-for="chunk in chunks"
                :key="chunk.milvus_id"
                class="border border-slate-200 rounded-lg overflow-hidden"
              >
                <div
                  class="flex items-center justify-between px-3 py-2 bg-slate-50 border-b border-slate-200"
                >
                  <span class="text-xs font-medium text-slate-500">
                    分块 {{ chunk.chunk_id + 1 }} / {{ chunk.total_chunks || '?' }}
                  </span>
                  <button
                    @click="copyChunk(chunk.content)"
                    class="text-xs text-slate-400 hover:text-blue-500 transition"
                    title="复制"
                  >
                    复制
                  </button>
                </div>
                <pre
                  class="p-3 text-xs text-slate-700 whitespace-pre-wrap font-mono leading-relaxed max-h-48 overflow-y-auto"
                  >{{ chunk.content }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ====== 上传 Tab ====== -->
      <div v-if="activeTab === 'upload'" class="max-w-lg mx-auto">
        <!-- 拖拽区域 -->
        <div
          class="border-2 border-dashed rounded-xl p-8 text-center transition cursor-pointer"
          :class="
            dragOver
              ? 'border-blue-400 bg-blue-50'
              : 'border-slate-300 hover:border-blue-300 hover:bg-slate-50'
          "
          @click="triggerFileInput"
          @dragover.prevent="dragOver = true"
          @dragleave="dragOver = false"
          @drop.prevent="onDrop"
        >
          <input
            ref="fileInputRef"
            type="file"
            accept=".txt"
            class="hidden"
            @change="onFileSelect"
          />
          <svg
            class="w-10 h-10 mx-auto text-slate-400 mb-3"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="1.5"
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
            />
          </svg>
          <p class="text-sm text-slate-600 mb-1">
            {{ uploadFile ? uploadFile.name : '点击选择或拖拽文件到此处' }}
          </p>
          <p class="text-xs text-slate-400">支持 .txt 格式（UTF-8 编码）</p>
        </div>

        <!-- 元数据表单 -->
        <div class="mt-6 space-y-4">
          <div>
            <label class="block text-xs font-medium text-slate-600 mb-1">文档类型</label>
            <select
              v-model="uploadDocType"
              class="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option v-for="opt in docTypeOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-600 mb-1">疾病名称</label>
            <input
              v-model="uploadDisease"
              placeholder="例如：高血压"
              class="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-600 mb-1">来源</label>
            <input
              v-model="uploadSource"
              placeholder="例如：用户上传"
              class="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>

        <!-- 上传按钮 -->
        <button
          @click="handleUpload"
          :disabled="!uploadFile || uploading"
          class="mt-6 w-full py-3 bg-blue-500 text-white rounded-xl text-sm font-medium hover:bg-blue-600 disabled:bg-slate-300 transition"
        >
          {{ uploading ? '上传中...' : '上传到知识库' }}
        </button>

        <!-- 结果提示 -->
        <div
          v-if="uploadResult"
          class="mt-4 p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700"
        >
          {{ uploadResult }}
        </div>
        <div
          v-if="uploadError"
          class="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700"
        >
          {{ uploadError }}
        </div>
      </div>
    </div>
  </div>
</template>
