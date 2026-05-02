import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage } from '../types'
import { useSSE } from '../composables/useSSE'

let msgIdCounter = 0
function genId() {
  return `msg-${Date.now()}-${++msgIdCounter}`
}

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const swarmMode = ref(true)
  const error = ref<string | null>(null)

  const { connect, disconnect } = useSSE()

  async function sendMessage(question: string) {
    if (isStreaming.value || !question.trim()) return

    error.value = null

    // 添加用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: 'user',
      content: question,
      timestamp: new Date().toISOString(),
    }
    messages.value.push(userMsg)

    // 创建 assistant 占位消息
    const assistantMsg: ChatMessage = {
      id: genId(),
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      isStreaming: true,
      agentEvents: [],
      suggestions: [],
      disclaimer: '',
    }
    messages.value.push(assistantMsg)

    isStreaming.value = true

    try {
      await connect('/api/chat/stream', {
        question,
        session_id: sessionId.value,
        enable_swarm: swarmMode.value,
      }, {
        onStart(data) {
          sessionId.value = data.session_id
        },
        onTaskDecomposed(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg?.agentEvents) {
            msg.agentEvents.push({
              id: `evt-${Date.now()}`,
              type: 'decomposed',
              agentId: 'lead_agent',
              timestamp: new Date().toISOString(),
              data,
            })
          }
        },
        onAgentStart(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg?.agentEvents) {
            msg.agentEvents.push({
              id: `evt-${Date.now()}`,
              type: 'start',
              agentId: data.source_agent || data.agent_id || 'unknown',
              subtaskId: data.data?.subtask_id,
              subtaskType: data.data?.subtask_type,
              timestamp: new Date().toISOString(),
              data: data.data || data,
            })
          }
        },
        onAgentComplete(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg?.agentEvents) {
            msg.agentEvents.push({
              id: `evt-${Date.now()}`,
              type: 'complete',
              agentId: data.source_agent || data.agent_id || 'unknown',
              timestamp: new Date().toISOString(),
              data: data.data || data,
            })
          }
        },
        onSuggestions(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg) msg.suggestions = data.suggestions
        },
        onDone(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg) {
            msg.content = data.answer || msg.content
            msg.disclaimer = data.disclaimer || ''
            msg.isStreaming = false
            msg.metadata = {
              swarmEnabled: data.swarm_enabled,
              agentsInvolved: data.agents_involved || [],
              totalTime: data.total_time,
            }
          }
          isStreaming.value = false
        },
        onError(data) {
          error.value = data.error
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg) {
            msg.content = `错误：${data.error}`
            msg.isStreaming = false
          }
          isStreaming.value = false
        },
        onStreamEnd() {
          // 流结束但未收到 done 事件的 fallback
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg && msg.isStreaming) {
            if (!msg.content) {
              msg.content = '请求已结束，但未收到完整响应。请重试。'
            }
            msg.isStreaming = false
            isStreaming.value = false
          }
        },
      })
    } catch (e: any) {
      if (e.name === 'AbortError') return
      error.value = e.message
      const msg = messages.value.find((m) => m.id === assistantMsg.id)
      if (msg) {
        msg.content = `请求失败：${e.message}`
        msg.isStreaming = false
      }
      isStreaming.value = false
    }
  }

  function clearChat() {
    messages.value = []
    sessionId.value = null
    error.value = null
    disconnect()
    isStreaming.value = false
  }

  return { sessionId, messages, isStreaming, swarmMode, error, sendMessage, clearChat }
})
