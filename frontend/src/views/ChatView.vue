<script setup lang="ts">
import { ref, nextTick, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useChatStore } from '../stores/chat'
import ChatInput from '../components/chat/ChatInput.vue'
import ChatMessage from '../components/chat/ChatMessage.vue'

const route = useRoute()
const chatStore = useChatStore()
const messagesContainer = ref<HTMLElement | null>(null)

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

async function handleSend(question: string) {
  await chatStore.sendMessage(question)
  scrollToBottom()
}

watch(() => chatStore.messages.length, scrollToBottom)

onMounted(() => {
  const sessionId = route.params.sessionId as string
  if (sessionId) {
    chatStore.sessionId = sessionId
  }
})
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 消息列表 -->
    <div ref="messagesContainer" class="flex-1 overflow-y-auto">
      <!-- 空状态 -->
      <div v-if="chatStore.messages.length === 0" class="h-full flex flex-col items-center justify-center text-slate-400">
        <svg class="w-16 h-16 mb-4 text-slate-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
        </svg>
        <h2 class="text-xl font-semibold text-slate-600 mb-2">MediZJ 医疗助手</h2>
        <p class="text-sm max-w-md text-center">
          基于多智能体 Swarm 架构的医疗咨询系统。<br/>
          请输入您的健康问题，系统将自动选择最合适的 Agent 为您服务。
        </p>
        <div class="flex flex-wrap gap-2 mt-6 max-w-lg justify-center">
          <button
            v-for="q in ['高血压患者饮食注意事项', '头疼发烧是怎么回事', '糖尿病最新临床指南']"
            :key="q"
            @click="handleSend(q)"
            class="text-xs px-3 py-2 bg-white border border-slate-200 rounded-lg hover:border-blue-300 hover:text-blue-600 transition"
          >
            {{ q }}
          </button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div v-else>
        <ChatMessage
          v-for="msg in chatStore.messages"
          :key="msg.id"
          :message="msg"
        />
      </div>
    </div>

    <!-- 输入框 -->
    <ChatInput :disabled="chatStore.isStreaming" @send="handleSend" />
  </div>
</template>
