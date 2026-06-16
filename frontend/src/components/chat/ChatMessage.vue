<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import { useMarkdown } from '../../composables/useMarkdown'
import { useChatStore } from '../../stores/chat'
import type { ChatMessage, ThinkingBlock } from '../../types'
import SuggestionChips from './SuggestionChips.vue'
import DisclaimerBanner from './DisclaimerBanner.vue'
import ThinkingBlockItem from './ThinkingBlock.vue'
import QuestionnaireCard from './QuestionnaireCard.vue'
import CitationPopover from './CitationPopover.vue'

const props = defineProps<{
  message: ChatMessage
}>()

const chatStore = useChatStore()
const { render } = useMarkdown()

const renderedContent = computed(() => {
  if (!props.message.content) return ''
  return render(props.message.content)
})

const isUser = computed(() => props.message.role === 'user')

// 引用 Popover 状态
const activeCitationRefs = ref<number[]>([])
const citationAnchorEl = ref<HTMLElement | null>(null)

function handleCitationClick(event: MouseEvent) {
  const target = event.target as HTMLElement
  if (!target.classList.contains('citation-ref')) return
  const refsStr = target.getAttribute('data-refs')
  if (!refsStr) return
  const refNumbers = refsStr.split(',').map(Number).filter(n => !isNaN(n))
  activeCitationRefs.value = refNumbers
  citationAnchorEl.value = target
}

function closeCitationPopover() {
  activeCitationRefs.value = []
  citationAnchorEl.value = null
}

// 流式内容更新后，等待 DOM 更新
watch(() => props.message.content, () => {
  nextTick(() => {
    closeCitationPopover()
  })
})

const agentNameMap: Record<string, string> = {
  swarm_coordinator: '汇总输出',
  consultation_agent: '健康咨询',
  diagnostic_agent: '症状诊断',
  research_agent: '医学研究',
}

// 按 agentId 分组 thinkingBlocks
const groupedThinkingBlocks = computed(() => {
  if (!props.message.thinkingBlocks || props.message.thinkingBlocks.length === 0) return []
  const groups: { agentId: string; agentName: string; blocks: ThinkingBlock[]; isActive: boolean }[] = []
  const map: Record<string, number> = {}
  for (const block of props.message.thinkingBlocks) {
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

// Agent 分组展开/收起状态（默认展开）
const collapsedGroups = ref<Record<string, boolean>>({})

function toggleGroup(agentId: string) {
  collapsedGroups.value[agentId] = !collapsedGroups.value[agentId]
}

function isGroupCollapsed(agentId: string): boolean {
  return collapsedGroups.value[agentId] === true
}

// 流式输出结束后自动折叠所有分组框
watch(() => props.message.isStreaming, (val, oldVal) => {
  if (oldVal === true && val === false) {
    for (const group of groupedThinkingBlocks.value) {
      collapsedGroups.value[group.agentId] = true
    }
  }
})
</script>

<template>
  <div class="py-4" :class="isUser ? '' : ''">
    <div class="max-w-4xl mx-auto px-4">
      <!-- 用户消息 -->
      <div v-if="isUser" class="flex justify-end">
        <div class="bg-blue-500 text-white px-4 py-2.5 rounded-2xl rounded-br-md max-w-[80%] text-sm leading-relaxed whitespace-pre-wrap">
          {{ message.content }}
        </div>
      </div>

      <!-- 助手消息 -->
      <div v-else class="space-y-3">
        <!-- 回答内容 -->
        <div class="flex gap-3">
          <div class="w-8 h-8 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center shrink-0 text-xs font-semibold">AI</div>
          <div class="flex-1 min-w-0">
            <div v-if="message.isStreaming && !message.content" class="flex items-center gap-1 text-slate-400 text-sm">
              <span class="animate-pulse">思考中</span>
              <span class="flex gap-0.5">
                <span class="w-1 h-1 bg-slate-400 rounded-full animate-bounce" style="animation-delay:0ms" />
                <span class="w-1 h-1 bg-slate-400 rounded-full animate-bounce" style="animation-delay:150ms" />
                <span class="w-1 h-1 bg-slate-400 rounded-full animate-bounce" style="animation-delay:300ms" />
              </span>
            </div>

            <!-- Thinking 内容块（按 Agent 分组） -->
            <div v-if="groupedThinkingBlocks.length > 0" class="space-y-2 mt-2">
              <div
                v-for="group in groupedThinkingBlocks"
                :key="group.agentId"
                class="border border-slate-200 rounded-lg overflow-hidden text-xs"
              >
                <!-- Agent 分组标题（可点击展开/收起） -->
                <button
                  @click="toggleGroup(group.agentId)"
                  class="w-full flex items-center gap-2 px-3 py-2 bg-slate-100 hover:bg-slate-200 transition text-left border-b border-slate-200"
                >
                  <!-- 展开/折叠箭头 -->
                  <svg
                    class="w-3 h-3 text-slate-400 transition-transform shrink-0"
                    :class="{ 'rotate-90': !isGroupCollapsed(group.agentId) }"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" />
                  </svg>
                  <span class="w-2 h-2 rounded-full" :class="group.isActive ? 'bg-blue-400 animate-pulse' : 'bg-green-400'" />
                  <span class="font-medium text-slate-600">{{ group.agentName }}</span>
                  <span class="text-slate-400">({{ group.blocks.length }} 次迭代)</span>
                  <span v-if="group.isActive" class="ml-auto flex gap-0.5">
                    <span class="w-1 h-1 bg-blue-400 rounded-full animate-bounce" style="animation-delay:0ms" />
                    <span class="w-1 h-1 bg-blue-400 rounded-full animate-bounce" style="animation-delay:150ms" />
                    <span class="w-1 h-1 bg-blue-400 rounded-full animate-bounce" style="animation-delay:300ms" />
                  </span>
                </button>
                <!-- 各迭代子块（收起时隐藏） -->
                <div v-if="!isGroupCollapsed(group.agentId)" class="p-1.5 space-y-1.5 bg-slate-50">
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
              v-if="message.questionnaire"
              :questionnaire="message.questionnaire"
              @submit="(answers) => chatStore.submitAnswers(message.questionnaire?.questionnaire_id || '', answers)"
              class="mt-3"
            />

            <!-- 建议 -->
            <SuggestionChips
              v-if="message.suggestions && message.suggestions.length > 0"
              :suggestions="message.suggestions"
              class="mt-3"
            />

            <!-- 免责声明 -->
            <DisclaimerBanner
              v-if="message.disclaimer"
              :text="message.disclaimer"
              class="mt-3"
            />

            <!-- 元信息 -->
            <div v-if="message.metadata" class="mt-2 flex items-center gap-3 text-xs text-slate-400">
              <span v-if="message.metadata.totalTime">
                {{ message.metadata.totalTime.toFixed(1) }}s
              </span>
              <span v-if="message.metadata.agentsInvolved.length">
                {{ message.metadata.agentsInvolved.length }} 个 Agent · {{ message.metadata.agentsInvolved.map(id => agentNameMap[id] || id).join('、') }}
              </span>
              <span v-if="message.metadata.usage?.total_tokens">
                {{ message.metadata.usage.total_tokens }} tokens
              </span>
              <template v-if="message.metadata.performanceMetrics">
                <span class="text-slate-300">|</span>
                <span>并行效率 {{ (message.metadata.performanceMetrics.parallelEfficiency * 100).toFixed(0) }}%</span>
                <span>子任务覆盖 {{ (message.metadata.performanceMetrics.informationCoverage * 100).toFixed(0) }}%</span>
                <span>信息冗余 {{ (message.metadata.performanceMetrics.redundancy * 100).toFixed(0) }}%</span>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
