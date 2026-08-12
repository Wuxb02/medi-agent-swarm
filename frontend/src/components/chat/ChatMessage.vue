<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { useMarkdown } from '../../composables/useMarkdown'
import { useChatStore } from '../../stores/chat'
import type { ChatMessage, ThinkingBlock } from '../../types'
import ThinkingBlockItem from './ThinkingBlock.vue'
import QuestionnaireCard from './QuestionnaireCard.vue'
import CitationPopover from './CitationPopover.vue'
import { getFeedback, submitFeedback, type FeedbackRating } from '../../api/evolution'

const props = defineProps<{
  message: ChatMessage
  showDisclaimer?: boolean
}>()

defineEmits<{
  'preview-image': [url: string]
}>()

const chatStore = useChatStore()
const { render } = useMarkdown()

const renderedContent = computed(() => {
  if (!props.message.content) return ''
  return render(props.message.content)
})

const isUser = computed(() => props.message.role === 'user')
const feedbackRating = ref<FeedbackRating | null>(null)
const feedbackOpen = ref(false)
const feedbackSaving = ref(false)
const feedbackReasons = ref<string[]>([])
const feedbackComment = ref('')
const reasonOptions = [
  ['unsafe', '医学安全'],
  ['incomplete', '回答不完整'],
  ['incorrect', '内容不准确'],
  ['tool_misuse', '工具使用不当'],
  ['not_personalized', '不够个性化'],
] as const

async function loadFeedback() {
  if (!props.message.assistantMessageId) return
  try {
    const feedback = await getFeedback(props.message.assistantMessageId)
    if (feedback) {
      feedbackRating.value = feedback.rating
      feedbackReasons.value = feedback.reason_codes
      feedbackComment.value = feedback.comment
    }
  } catch {
    // 反馈状态不影响对话展示。
  }
}

async function rate(rating: FeedbackRating) {
  feedbackRating.value = rating
  feedbackOpen.value = rating === 'dislike'
  if (rating === 'like') await saveFeedback()
}

async function saveFeedback() {
  if (!props.message.assistantMessageId || !feedbackRating.value || feedbackSaving.value) return
  feedbackSaving.value = true
  try {
    await submitFeedback(
      props.message.assistantMessageId,
      feedbackRating.value,
      feedbackReasons.value,
      feedbackComment.value,
    )
    feedbackOpen.value = false
  } finally {
    feedbackSaving.value = false
  }
}

watch(() => props.message.assistantMessageId, loadFeedback, { immediate: true })

// 引用 Popover 状态
const activeCitationRefs = ref<number[]>([])
const citationAnchorEl = ref<HTMLElement | null>(null)

function handleCitationClick(event: MouseEvent) {
  const target = event.target as HTMLElement
  if (!target.classList.contains('citation-ref')) return
  const refsStr = target.getAttribute('data-refs')
  if (!refsStr) return
  const refNumbers = refsStr
    .split(',')
    .map(Number)
    .filter((n) => !isNaN(n))
  activeCitationRefs.value = refNumbers
  citationAnchorEl.value = target
}

function closeCitationPopover() {
  activeCitationRefs.value = []
  citationAnchorEl.value = null
}

// 流式内容更新后，等待 DOM 更新
watch(
  () => props.message.content,
  () => {
    nextTick(() => {
      closeCitationPopover()
    })
  },
)

const agentNameMap: Record<string, string> = {
  swarm_coordinator: '汇总输出',
  lead_agent: '任务协调',
  consultation_agent: '健康咨询',
  diagnostic_agent: '症状诊断',
  research_agent: '医学研究',
}

// 拆分为 LeadAgent 和 WorkerAgent 的 thinking blocks
const leadBlocks = computed(() =>
  (props.message.thinkingBlocks || []).filter((b) => b.agentId === 'lead_agent'),
)

const leadIntentBlocks = computed(() => leadBlocks.value.filter((b) => b.phase === 'intent'))

const leadClarifyBlocks = computed(() => leadBlocks.value.filter((b) => b.phase === 'clarify'))

const leadDecomposeBlocks = computed(() =>
  leadBlocks.value.filter((b) => b.phase === 'decompose' || (!b.phase && b.iteration === 1)),
)

const leadSynthesizeBlocks = computed(() =>
  leadBlocks.value.filter((b) => b.phase === 'synthesize' || (!b.phase && b.iteration === 2)),
)

const workerBlocks = computed(() =>
  (props.message.thinkingBlocks || []).filter(
    (b) => b.agentId !== 'lead_agent' && b.agentId !== 'swarm_coordinator',
  ),
)

// WorkerAgent 分组（按 agentId）
const workerGroups = computed(() => {
  if (workerBlocks.value.length === 0) return []
  const groups: {
    agentId: string
    agentName: string
    blocks: ThinkingBlock[]
    isActive: boolean
  }[] = []
  const map: Record<string, number> = {}
  for (const block of workerBlocks.value) {
    if (map[block.agentId] === undefined) {
      map[block.agentId] = groups.length
      groups.push({
        agentId: block.agentId,
        agentName: agentNameMap[block.agentId] || block.agentId,
        blocks: [block],
        isActive: !block.isCollapsed,
      })
    } else {
      const g = groups[map[block.agentId]]
      g.blocks.push(block)
      if (!block.isCollapsed) g.isActive = true
    }
  }
  return groups
})

// LeadAgent 折叠状态
const leadCollapsed = ref(false)

// Worker 分组折叠状态
const collapsedWorkerGroups = ref<Record<string, boolean>>({})

// "Agent 执行过程" 整体折叠状态
const workerSectionCollapsed = ref(false)

function toggleWorkerGroup(agentId: string) {
  collapsedWorkerGroups.value[agentId] = !collapsedWorkerGroups.value[agentId]
}

function isWorkerCollapsed(agentId: string): boolean {
  return collapsedWorkerGroups.value[agentId] === true
}

// 流式输出结束后自动折叠所有分组框
watch(
  () => props.message.isStreaming,
  (val, oldVal) => {
    if (oldVal === true && val === false) {
      leadCollapsed.value = true
      workerSectionCollapsed.value = true
      for (const group of workerGroups.value) {
        collapsedWorkerGroups.value[group.agentId] = true
      }
    }
  },
)
</script>

<template>
  <div class="py-4" :class="isUser ? '' : ''">
    <div class="max-w-4xl mx-auto px-4">
      <!-- 用户消息 -->
      <div v-if="isUser" class="flex justify-end">
        <div
          class="bg-blue-500 text-white px-4 py-2.5 rounded-2xl rounded-br-md max-w-[80%] text-sm leading-relaxed"
        >
          <!-- 图片缩略图 -->
          <div v-if="message.images?.length" class="flex gap-1 mb-2 flex-wrap justify-end">
            <img
              v-for="(url, idx) in message.images"
              :key="idx"
              :src="url"
              class="max-w-[200px] max-h-[200px] rounded-lg object-cover border border-white/30 cursor-pointer hover:opacity-80 transition"
              alt="用户上传图片"
              @click="$emit('preview-image', url)"
            />
          </div>
          <!-- 文本内容 -->
          <div class="whitespace-pre-wrap">{{ message.content }}</div>
        </div>
      </div>

      <!-- 助手消息 -->
      <div v-else class="space-y-3">
        <!-- 回答内容 -->
        <div class="flex gap-3">
          <div
            class="w-8 h-8 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center shrink-0 text-xs font-semibold"
          >
            AI
          </div>
          <div class="flex-1 min-w-0">
            <div
              v-if="message.isStreaming && !message.content"
              class="flex items-center gap-1 text-slate-400 text-sm"
            >
              <span class="animate-pulse">思考中</span>
              <span class="flex gap-0.5">
                <span
                  class="w-1 h-1 bg-slate-400 rounded-full animate-bounce"
                  style="animation-delay: 0ms"
                />
                <span
                  class="w-1 h-1 bg-slate-400 rounded-full animate-bounce"
                  style="animation-delay: 150ms"
                />
                <span
                  class="w-1 h-1 bg-slate-400 rounded-full animate-bounce"
                  style="animation-delay: 300ms"
                />
              </span>
            </div>

            <!-- Thinking 内容块 -->
            <!-- Swarm/单Agent 嵌套布局：LeadAgent 作为外层容器，WorkerAgent 内嵌 -->
            <div v-if="leadBlocks.length > 0" class="space-y-2 mt-2">
              <div class="border border-blue-200 rounded-lg overflow-hidden text-xs bg-blue-50/30">
                <!-- LeadAgent 标题栏 -->
                <button
                  @click="leadCollapsed = !leadCollapsed"
                  class="w-full flex items-center gap-2 px-3 py-2 bg-blue-100 hover:bg-blue-200 transition text-left border-b border-blue-200"
                >
                  <svg
                    class="w-3 h-3 text-blue-500 transition-transform shrink-0"
                    :class="{ 'rotate-90': !leadCollapsed }"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path
                      fill-rule="evenodd"
                      d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
                    />
                  </svg>
                  <span
                    class="w-2 h-2 rounded-full"
                    :class="
                      leadBlocks.some((b) => !b.isCollapsed)
                        ? 'bg-blue-400 animate-pulse'
                        : 'bg-blue-400'
                    "
                  />
                  <span class="font-medium text-blue-700">任务协调</span>
                  <span class="text-blue-500">({{ leadBlocks.length }} 个阶段)</span>
                  <span v-if="leadBlocks.some((b) => !b.isCollapsed)" class="ml-auto flex gap-0.5">
                    <span
                      class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
                      style="animation-delay: 0ms"
                    />
                    <span
                      class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
                      style="animation-delay: 150ms"
                    />
                    <span
                      class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
                      style="animation-delay: 300ms"
                    />
                  </span>
                </button>

                <!-- LeadAgent 自身思考 + 委派信息 + WorkerAgent 思考 -->
                <div v-if="!leadCollapsed" class="p-2 space-y-1.5">
                  <!-- 1. 意图识别 -->
                  <div v-if="leadIntentBlocks.length > 0" class="space-y-1.5">
                    <ThinkingBlockItem
                      v-for="block in leadIntentBlocks"
                      :key="block.id"
                      :thinking="block.thinking"
                      :agent-id="block.agentId"
                      :iteration="block.iteration"
                      :tool-steps="block.toolSteps"
                      :elapsed-seconds="block.elapsedSeconds"
                      :is-collapsed="block.isCollapsed"
                      :status="block.status"
                      :label="block.title || '意图识别'"
                    />
                  </div>

                  <!-- 2. 信息澄清 -->
                  <div v-if="leadClarifyBlocks.length > 0" class="space-y-1.5">
                    <ThinkingBlockItem
                      v-for="block in leadClarifyBlocks"
                      :key="block.id"
                      :thinking="block.thinking"
                      :agent-id="block.agentId"
                      :iteration="block.iteration"
                      :tool-steps="block.toolSteps"
                      :elapsed-seconds="block.elapsedSeconds"
                      :is-collapsed="block.isCollapsed"
                      :status="block.status"
                      :label="block.title || `信息澄清（第 ${block.iteration} 轮）`"
                    />
                    <QuestionnaireCard
                      v-if="message.questionnaire"
                      :questionnaire="message.questionnaire"
                      :error="message.questionnaireError"
                      @submit="
                        (answers) =>
                          chatStore.submitAnswers(
                            message.questionnaire?.questionnaire_id || '',
                            answers,
                          )
                      "
                    />
                  </div>

                  <!-- 3. 分解任务 -->
                  <div v-if="leadDecomposeBlocks.length > 0" class="space-y-1.5">
                    <ThinkingBlockItem
                      v-for="block in leadDecomposeBlocks"
                      :key="block.id"
                      :thinking="block.thinking"
                      :agent-id="block.agentId"
                      :iteration="block.iteration"
                      :tool-steps="block.toolSteps"
                      :elapsed-seconds="block.elapsedSeconds"
                      :is-collapsed="block.isCollapsed"
                      :status="block.status"
                      :label="block.title || '分解任务'"
                    />
                  </div>

                  <!-- 4. WorkerAgent 执行过程 -->
                  <div
                    v-if="workerGroups.length > 0"
                    class="border border-slate-200 rounded overflow-hidden bg-white"
                  >
                    <button
                      @click="workerSectionCollapsed = !workerSectionCollapsed"
                      class="w-full flex items-center gap-2 px-3 py-1.5 text-slate-500 hover:bg-slate-50 transition text-left"
                    >
                      <svg
                        class="w-3 h-3 text-slate-400 transition-transform shrink-0"
                        :class="{ 'rotate-90': !workerSectionCollapsed }"
                        fill="currentColor"
                        viewBox="0 0 20 20"
                      >
                        <path
                          fill-rule="evenodd"
                          d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
                        />
                      </svg>
                      <span>Agent 执行过程</span>
                      <span
                        class="rounded px-1.5 py-0.5 text-[10px]"
                        :class="
                          workerGroups.some((group) => group.isActive)
                            ? 'bg-blue-50 text-blue-600'
                            : 'bg-green-50 text-green-600'
                        "
                      >
                        {{ workerGroups.some((group) => group.isActive) ? '进行中' : '已完成' }}
                      </span>
                    </button>
                    <div
                      v-if="!workerSectionCollapsed"
                      class="px-3 py-2 border-t border-slate-100 space-y-1.5"
                    >
                      <div
                        v-for="wGroup in workerGroups"
                        :key="wGroup.agentId"
                        class="border-l-2 border-blue-100 pl-3"
                      >
                        <!-- Worker 分组标题 -->
                        <button
                          @click="toggleWorkerGroup(wGroup.agentId)"
                          class="w-full flex items-center gap-1.5 py-1 text-left hover:opacity-80 transition"
                        >
                          <svg
                            class="w-2.5 h-2.5 text-slate-400 transition-transform shrink-0"
                            :class="{ 'rotate-90': !isWorkerCollapsed(wGroup.agentId) }"
                            fill="currentColor"
                            viewBox="0 0 20 20"
                          >
                            <path
                              fill-rule="evenodd"
                              d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
                            />
                          </svg>
                          <span
                            class="w-1.5 h-1.5 rounded-full"
                            :class="wGroup.isActive ? 'bg-green-400 animate-pulse' : 'bg-green-400'"
                          />
                          <span class="font-medium text-slate-600">{{ wGroup.agentName }}</span>
                          <span class="text-slate-400">({{ wGroup.blocks.length }} 次迭代)</span>
                          <span v-if="wGroup.isActive" class="flex gap-0.5 ml-1">
                            <span
                              class="w-1 h-1 bg-green-400 rounded-full animate-bounce"
                              style="animation-delay: 0ms"
                            />
                            <span
                              class="w-1 h-1 bg-green-400 rounded-full animate-bounce"
                              style="animation-delay: 150ms"
                            />
                            <span
                              class="w-1 h-1 bg-green-400 rounded-full animate-bounce"
                              style="animation-delay: 300ms"
                            />
                          </span>
                        </button>
                        <!-- Worker 迭代块 -->
                        <div v-if="!isWorkerCollapsed(wGroup.agentId)" class="space-y-1 pt-0.5">
                          <ThinkingBlockItem
                            v-for="block in wGroup.blocks"
                            :key="block.id"
                            :thinking="block.thinking"
                            :agent-id="block.agentId"
                            :iteration="block.iteration"
                            :tool-steps="block.toolSteps"
                            :elapsed-seconds="block.elapsedSeconds"
                            :is-collapsed="block.isCollapsed"
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- 5. 结果汇总 -->
                  <div v-if="leadSynthesizeBlocks.length > 0" class="space-y-1.5">
                    <ThinkingBlockItem
                      v-for="block in leadSynthesizeBlocks"
                      :key="block.id"
                      :thinking="block.thinking"
                      :agent-id="block.agentId"
                      :iteration="block.iteration"
                      :tool-steps="block.toolSteps"
                      :elapsed-seconds="block.elapsedSeconds"
                      :is-collapsed="block.isCollapsed"
                      :status="block.status"
                      :label="block.title || '结果汇总'"
                    />
                  </div>
                </div>
              </div>
            </div>

            <!-- 非 Swarm 模式：保持扁平布局 -->
            <div v-else-if="workerGroups.length > 0" class="space-y-2 mt-2">
              <div
                v-for="group in workerGroups"
                :key="group.agentId"
                class="border border-slate-200 rounded-lg overflow-hidden text-xs"
              >
                <!-- Agent 分组标题（可点击展开/收起） -->
                <button
                  @click="toggleWorkerGroup(group.agentId)"
                  class="w-full flex items-center gap-2 px-3 py-2 bg-slate-100 hover:bg-slate-200 transition text-left border-b border-slate-200"
                >
                  <!-- 展开/折叠箭头 -->
                  <svg
                    class="w-3 h-3 text-slate-400 transition-transform shrink-0"
                    :class="{ 'rotate-90': !isWorkerCollapsed(group.agentId) }"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path
                      fill-rule="evenodd"
                      d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z"
                    />
                  </svg>
                  <span
                    class="w-2 h-2 rounded-full"
                    :class="group.isActive ? 'bg-blue-400 animate-pulse' : 'bg-green-400'"
                  />
                  <span class="font-medium text-slate-600">{{ group.agentName }}</span>
                  <span class="text-slate-400">({{ group.blocks.length }} 次迭代)</span>
                  <span v-if="group.isActive" class="ml-auto flex gap-0.5">
                    <span
                      class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
                      style="animation-delay: 0ms"
                    />
                    <span
                      class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
                      style="animation-delay: 150ms"
                    />
                    <span
                      class="w-1 h-1 bg-blue-400 rounded-full animate-bounce"
                      style="animation-delay: 300ms"
                    />
                  </span>
                </button>
                <!-- 各迭代子块（收起时隐藏） -->
                <div v-if="!isWorkerCollapsed(group.agentId)" class="p-1.5 space-y-1.5 bg-slate-50">
                  <ThinkingBlockItem
                    v-for="block in group.blocks"
                    :key="block.id"
                    :thinking="block.thinking"
                    :agent-id="block.agentId"
                    :iteration="block.iteration"
                    :tool-steps="block.toolSteps"
                    :elapsed-seconds="block.elapsedSeconds"
                    :is-collapsed="block.isCollapsed"
                  />
                </div>
              </div>
            </div>

            <div
              v-if="message.content"
              class="markdown-body text-sm text-slate-700 leading-relaxed mt-2"
              v-html="renderedContent"
              @click="handleCitationClick"
            />

            <!-- 引用 Popover -->
            <CitationPopover
              v-if="activeCitationRefs.length > 0 && citationAnchorEl"
              :citations="message.citations || []"
              :ref-numbers="activeCitationRefs"
              :anchor-el="citationAnchorEl"
              @close="closeCitationPopover"
            />

            <!-- 交互式问卷 -->
            <QuestionnaireCard
              v-if="message.questionnaire && leadClarifyBlocks.length === 0"
              :questionnaire="message.questionnaire"
              :error="message.questionnaireError"
              @submit="
                (answers) =>
                  chatStore.submitAnswers(message.questionnaire?.questionnaire_id || '', answers)
              "
              class="mt-3"
            />

            <!-- 免责声明（仅对话最后一条消息在元信息上方展示） -->
            <div
              v-if="showDisclaimer"
              class="mt-3 flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700"
            >
              <svg
                class="w-4 h-4 shrink-0 mt-0.5 text-amber-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"
                />
              </svg>
              <span>以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。</span>
            </div>

            <div
              v-if="message.assistantMessageId && !message.isStreaming"
              class="mt-2 text-xs text-slate-400"
            >
              <div class="flex items-center gap-1">
                <span class="mr-1">这个回答有帮助吗？</span>
                <button
                  class="rounded px-2 py-1 hover:bg-slate-100"
                  :class="feedbackRating === 'like' ? 'bg-blue-50 text-blue-600' : ''"
                  aria-label="有帮助"
                  @click="rate('like')"
                >
                  ♥ 有帮助
                </button>
                <button
                  class="rounded px-2 py-1 hover:bg-slate-100"
                  :class="feedbackRating === 'dislike' ? 'bg-red-50 text-red-600' : ''"
                  aria-label="无帮助"
                  @click="rate('dislike')"
                >
                  × 需改进
                </button>
              </div>
              <div v-if="feedbackOpen" class="mt-2 rounded-lg border border-slate-200 p-3">
                <div class="mb-2 flex flex-wrap gap-2">
                  <label v-for="option in reasonOptions" :key="option[0]" class="flex gap-1">
                    <input v-model="feedbackReasons" type="checkbox" :value="option[0]" />
                    {{ option[1] }}
                  </label>
                </div>
                <textarea
                  v-model="feedbackComment"
                  maxlength="1000"
                  class="w-full rounded border border-slate-200 p-2 text-slate-700"
                  placeholder="可选：告诉我们具体哪里需要改进"
                />
                <button
                  class="mt-2 rounded bg-blue-600 px-3 py-1.5 text-white disabled:opacity-50"
                  :disabled="feedbackSaving"
                  @click="saveFeedback"
                >
                  {{ feedbackSaving ? '提交中…' : '提交反馈' }}
                </button>
              </div>
            </div>

            <!-- 元信息 -->
            <div
              v-if="message.metadata"
              class="mt-2 flex items-center gap-3 text-xs text-slate-400"
            >
              <span v-if="message.metadata.totalTime">
                {{ message.metadata.totalTime.toFixed(1) }}s
              </span>
              <span v-if="message.metadata.agentsInvolved.length">
                {{ message.metadata.agentsInvolved.length }} 个 Agent ·
                {{ message.metadata.agentsInvolved.map((id) => agentNameMap[id] || id).join('、') }}
              </span>
              <span v-if="message.metadata.usage?.total_tokens">
                {{ message.metadata.usage.total_tokens }} tokens
              </span>
              <template v-if="message.metadata.performanceMetrics">
                <span class="text-slate-300">|</span>
                <span
                  >并行效率
                  {{
                    (message.metadata.performanceMetrics.parallelEfficiency * 100).toFixed(0)
                  }}%</span
                >
                <span
                  >子任务覆盖
                  {{
                    (message.metadata.performanceMetrics.informationCoverage * 100).toFixed(0)
                  }}%</span
                >
                <span
                  >信息冗余
                  {{ (message.metadata.performanceMetrics.redundancy * 100).toFixed(0) }}%</span
                >
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
