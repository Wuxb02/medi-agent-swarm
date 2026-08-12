<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import type { QuestionnaireData } from '../../types'

const props = defineProps<{
  questionnaire: QuestionnaireData
  error?: string
}>()

const emit = defineEmits<{
  submit: [answers: Record<string, any>]
}>()

const answers = reactive<Record<string, any>>({})
const otherTexts = reactive<Record<string, string>>({})
const submitted = ref(false)
const activeTab = ref(0)

// 提交失败时重置提交态，允许重试（保留已填答案）
watch(
  () => props.error,
  (val) => {
    if (val) submitted.value = false
  },
)

const total = computed(() => props.questionnaire.questions.length)
const currentQ = computed(() => props.questionnaire.questions[activeTab.value])
const currentKey = computed(() => `q${activeTab.value}`)

// 初始化答案
for (let i = 0; i < total.value; i++) {
  const q = props.questionnaire.questions[i]
  answers[`q${i}`] = q.type === 'multi' ? [] : ''
}

// 当前问题是否已回答
function isAnswered(idx: number): boolean {
  const q = props.questionnaire.questions[idx]
  const key = `q${idx}`
  const val = answers[key]
  const other = otherTexts[key]?.trim()
  if (q.type === 'multi') return (Array.isArray(val) && val.length > 0) || !!other
  return (!!val && String(val).trim() !== '') || !!other
}

// 必填项是否全部完成
function allRequiredAnswered(): boolean {
  for (let i = 0; i < total.value; i++) {
    if (props.questionnaire.questions[i].required && !isAnswered(i)) return false
  }
  return true
}

function toggleMulti(key: string, label: string) {
  const arr = answers[key] as string[]
  const idx = arr.indexOf(label)
  if (idx >= 0) arr.splice(idx, 1)
  else arr.push(label)
}

function isMultiSelected(key: string, label: string): boolean {
  const val = answers[key]
  return Array.isArray(val) && val.includes(label)
}

function prev() {
  if (activeTab.value > 0) activeTab.value--
}

function next() {
  if (activeTab.value < total.value - 1) activeTab.value++
}

function handleSubmit() {
  if (!allRequiredAnswered() || submitted.value) return
  submitted.value = true
  // 合并 otherTexts 到 answers
  const merged = { ...answers }
  for (const key in otherTexts) {
    const other = otherTexts[key]?.trim()
    if (!other) continue
    const q = props.questionnaire.questions[parseInt(key.slice(1))]
    if (q?.type === 'multi' && Array.isArray(merged[key])) {
      merged[key] = [...merged[key], other]
    } else if (q?.type === 'enum') {
      merged[key] = other
    } else {
      merged[key] = other
    }
  }
  emit('submit', merged)
}
</script>

<template>
  <div class="qc-card">
    <!-- 头部 -->
    <div class="qc-header">
      <svg class="qc-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="2"
          d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      <span>请填写以下信息</span>
      <span class="qc-counter">{{ activeTab + 1 }} / {{ total }}</span>
    </div>

    <!-- Tab 导航 -->
    <div class="qc-tabs">
      <button
        v-for="(q, idx) in questionnaire.questions"
        :key="idx"
        class="qc-tab"
        :class="{
          active: idx === activeTab,
          done: isAnswered(idx),
          required: q.required,
        }"
        @click="activeTab = idx"
      >
        <span class="qc-tab-dot" />
        {{ q.header }}
      </button>
    </div>

    <!-- 当前问题 -->
    <div class="qc-body">
      <div class="qc-question-text">
        {{ currentQ.text }}
        <span v-if="currentQ.required" class="qc-required">*</span>
      </div>

      <!-- 单选 (enum) -->
      <div v-if="currentQ.type === 'enum'">
        <div class="qc-options">
          <label
            v-for="opt in currentQ.options"
            :key="opt.label"
            class="qc-opt"
            :class="{ selected: answers[currentKey] === opt.label }"
          >
            <input
              type="radio"
              :name="currentKey"
              :value="opt.label"
              v-model="answers[currentKey]"
              class="sr-only"
              @change="otherTexts[currentKey] = ''"
            />
            <span class="qc-radio" />
            <span class="qc-opt-text">
              {{ opt.label }}
              <small v-if="opt.description">{{ opt.description }}</small>
            </span>
          </label>
        </div>
        <div class="qc-other">
          <span class="qc-other-label">其他：</span>
          <input
            v-model="otherTexts[currentKey]"
            type="text"
            placeholder="以上没有符合的，手动输入"
            class="qc-other-input"
            @focus="answers[currentKey] = ''"
          />
        </div>
      </div>

      <!-- 多选 (multi) -->
      <div v-else-if="currentQ.type === 'multi'">
        <div class="qc-options">
          <label
            v-for="opt in currentQ.options"
            :key="opt.label"
            class="qc-opt"
            :class="{ selected: isMultiSelected(currentKey, opt.label) }"
          >
            <input
              type="checkbox"
              :value="opt.label"
              @change="toggleMulti(currentKey, opt.label)"
              class="sr-only"
            />
            <span class="qc-check" />
            <span class="qc-opt-text">
              {{ opt.label }}
              <small v-if="opt.description">{{ opt.description }}</small>
            </span>
          </label>
        </div>
        <div class="qc-other">
          <span class="qc-other-label">其他：</span>
          <input
            v-model="otherTexts[currentKey]"
            type="text"
            placeholder="补充选项中没有的内容"
            class="qc-other-input"
          />
        </div>
      </div>

      <!-- 文本输入 (input) -->
      <div v-else-if="currentQ.type === 'input'" class="qc-input-wrap">
        <input
          v-model="answers[currentKey]"
          type="text"
          :placeholder="`请输入${currentQ.header}...`"
          class="qc-input"
        />
      </div>
    </div>

    <!-- 底部导航 -->
    <div class="qc-footer">
      <button class="qc-nav-btn" :disabled="activeTab === 0" @click="prev">
        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M15 19l-7-7 7-7"
          />
        </svg>
        上一题
      </button>

      <div class="qc-dots">
        <span
          v-for="(_, idx) in questionnaire.questions"
          :key="idx"
          class="qc-dot"
          :class="{ active: idx === activeTab, done: isAnswered(idx) }"
          @click="activeTab = idx"
        />
      </div>

      <!-- 最后一题：显示提交按钮 -->
      <button
        v-if="activeTab === total - 1"
        class="qc-nav-btn qc-submit-btn"
        :class="{ disabled: !allRequiredAnswered() || submitted }"
        :disabled="!allRequiredAnswered() || submitted"
        @click="handleSubmit"
      >
        {{ submitted ? '已提交' : '提交' }}
      </button>
      <!-- 非最后一题：下一题 -->
      <button v-else class="qc-nav-btn qc-next-btn" @click="next">
        下一题
        <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
      </button>

      <!-- 提交失败提示 -->
      <div v-if="error" class="mt-2 text-xs text-red-600 flex items-center gap-1">
        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
        <span>{{ error }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qc-card {
  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
  border: 1px solid #bae6fd;
  border-radius: 12px;
  padding: 16px;
  margin: 8px 0;
  max-width: 480px;
}

.qc-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #0369a1;
  font-size: 15px;
  margin-bottom: 12px;
}

.qc-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.qc-counter {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: #7dd3fc;
}

/* Tab 导航条 */
.qc-tabs {
  display: flex;
  gap: 4px;
  overflow-x: auto;
  padding-bottom: 8px;
  margin-bottom: 12px;
  border-bottom: 1px solid #bae6fd;
}

.qc-tab {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  border: none;
  background: transparent;
  color: #64748b;
  transition: all 0.15s;
  flex-shrink: 0;
}

.qc-tab:hover {
  background: #e0f2fe;
}

.qc-tab.active {
  background: #0ea5e9;
  color: white;
  font-weight: 500;
}

.qc-tab-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cbd5e1;
  flex-shrink: 0;
}

.qc-tab.done .qc-tab-dot {
  background: #22c55e;
}
.qc-tab.active .qc-tab-dot {
  background: white;
}
.qc-tab.active.done .qc-tab-dot {
  background: #bbf7d0;
}

/* 问题区域 */
.qc-body {
  padding: 8px 0;
  min-height: 140px;
}

.qc-question-text {
  font-size: 15px;
  font-weight: 500;
  color: #1e293b;
  margin-bottom: 12px;
}

.qc-required {
  color: #ef4444;
  margin-left: 2px;
}

.qc-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.qc-opt {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  background: white;
  border: 1px solid #e2e8f0;
}

.qc-opt:hover {
  border-color: #7dd3fc;
  background: #f0f9ff;
}
.qc-opt.selected {
  border-color: #0ea5e9;
  background: #e0f2fe;
}

.sr-only {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  overflow: hidden;
  cursor: pointer;
}

.qc-opt:focus-within {
  border-color: #0ea5e9;
  box-shadow: 0 0 0 2px rgba(14, 165, 233, 0.12);
}

.qc-radio {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid #94a3b8;
  flex-shrink: 0;
  position: relative;
}
.qc-opt.selected .qc-radio {
  border-color: #0ea5e9;
}
.qc-opt.selected .qc-radio::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #0ea5e9;
}

.qc-check {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  border: 2px solid #94a3b8;
  flex-shrink: 0;
  position: relative;
}
.qc-opt.selected .qc-check {
  border-color: #0ea5e9;
  background: #0ea5e9;
}
.qc-opt.selected .qc-check::after {
  content: '';
  position: absolute;
  top: 1px;
  left: 4px;
  width: 5px;
  height: 9px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.qc-opt-text {
  font-size: 14px;
  color: #334155;
}
.qc-opt-text small {
  display: block;
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
}

/* 其他输入 */
.qc-other {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding: 8px 12px;
  background: white;
  border: 1px dashed #bae6fd;
  border-radius: 8px;
}

.qc-other-label {
  font-size: 13px;
  color: #64748b;
  white-space: nowrap;
  flex-shrink: 0;
}

.qc-other-input {
  flex: 1;
  border: none;
  outline: none;
  font-size: 14px;
  color: #334155;
  background: transparent;
}

.qc-other-input::placeholder {
  color: #94a3b8;
  font-size: 13px;
}

.qc-input-wrap {
  margin-top: 4px;
}

.qc-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  background: white;
  transition: border-color 0.15s;
}
.qc-input:focus {
  border-color: #0ea5e9;
  box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.1);
}

/* 底部导航 */
.qc-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #bae6fd;
}

.qc-nav-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  border: 1px solid #bae6fd;
  background: white;
  color: #0369a1;
  cursor: pointer;
  transition: all 0.15s;
}
.qc-nav-btn:hover:not(:disabled) {
  background: #f0f9ff;
}
.qc-nav-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.qc-next-btn {
  background: #0ea5e9;
  color: white;
  border-color: #0ea5e9;
}
.qc-next-btn:hover {
  background: #0284c7;
}

.qc-submit-btn {
  background: #0ea5e9;
  color: white;
  border-color: #0ea5e9;
}
.qc-submit-btn:hover:not(.disabled) {
  background: #0284c7;
}
.qc-submit-btn.disabled {
  background: #94a3b8;
  border-color: #94a3b8;
  cursor: not-allowed;
}

/* 进度圆点 */
.qc-dots {
  display: flex;
  gap: 6px;
}

.qc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cbd5e1;
  cursor: pointer;
  transition: all 0.15s;
}
.qc-dot.active {
  background: #0ea5e9;
  transform: scale(1.3);
}
.qc-dot.done {
  background: #22c55e;
}
</style>
