<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '../../composables/useMarkdown'
import type { ChatMessage } from '../../types'
import AgentTimeline from '../agents/AgentTimeline.vue'
import SuggestionChips from './SuggestionChips.vue'
import DisclaimerBanner from './DisclaimerBanner.vue'
import ThinkingBlock from './ThinkingBlock.vue'

const props = defineProps<{
  message: ChatMessage
}>()

const { render } = useMarkdown()

const renderedContent = computed(() => {
  if (!props.message.content) return ''
  return render(props.message.content)
})

const isUser = computed(() => props.message.role === 'user')
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
        <!-- Agent 协作时间线 -->
        <AgentTimeline
          v-if="message.agentEvents && message.agentEvents.length > 0"
          :events="message.agentEvents"
          :metadata="message.metadata"
        />

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

            <!-- Thinking 内容块（位于思考中下方） -->
            <div v-if="message.thinkingBlocks && message.thinkingBlocks.length > 0" class="space-y-2 mt-2">
              <ThinkingBlock
                v-for="block in message.thinkingBlocks"
                :key="block.id"
                :thinking="block.thinking"
                :agent-id="block.agentId"
                :iteration="block.iteration"
                :tool-steps="block.toolSteps"
                :elapsed-seconds="block.elapsedSeconds"
                :is-collapsed="block.isCollapsed"
              />
            </div>

            <div
              v-if="message.content"
              class="markdown-body text-sm text-slate-700 leading-relaxed mt-2"
              v-html="renderedContent"
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
              <span v-if="message.metadata.swarmEnabled" class="flex items-center gap-1">
                <span class="w-1.5 h-1.5 bg-green-400 rounded-full" />
                Swarm 协作
              </span>
              <span v-if="message.metadata.totalTime">
                {{ message.metadata.totalTime.toFixed(1) }}s
              </span>
              <span v-if="message.metadata.agentsInvolved.length">
                {{ message.metadata.agentsInvolved.length }} 个 Agent
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
