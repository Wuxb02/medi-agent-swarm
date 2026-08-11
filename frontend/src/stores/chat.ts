import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, SessionTurn } from '../types'
import { useSSE } from '../composables/useSSE'
import { getSessionDetail } from '../api/session'
import { createEventAggregator } from '../utils/eventAggregator'
import { typeRemainingText, type TypewriterController } from '../utils/typewriter'

let msgIdCounter = 0
function genId() {
  return `msg-${Date.now()}-${++msgIdCounter}`
}

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref<string | null>(null)
  const sessionTitle = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const error = ref<string | null>(null)
  let typewriter: TypewriterController | null = null

  const { connect, disconnect } = useSSE()

  async function sendMessage(question: string, images?: string[]) {
    if (isStreaming.value || (!question.trim() && !images?.length)) return

    error.value = null

    // 添加用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: 'user',
      content: question,
      images: images || [],
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
      thinkingBlocks: [],
      suggestions: [],
    }
    messages.value.push(assistantMsg)

    let isDone = false
    isStreaming.value = true

    // 实时流使用 eventAggregator 收集事件
    const aggregator = createEventAggregator(true)

    try {
      await connect(
        '/api/chat/stream',
        {
          question,
          session_id: sessionId.value,
          images: images?.length ? images : undefined,
        },
        {
          onStart(data) {
            sessionId.value = data.session_id
          },
          onTaskDecomposed(data) {
            aggregator.consume('task_decomposed', data as Record<string, unknown>)
            const snapshot = aggregator.getSnapshot()
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.agentEvents = [...snapshot.agentEvents]
              msg.delegations = [...snapshot.delegations]
            }
          },
          onAgentStart(data) {
            aggregator.consume('agent_start', data as Record<string, unknown>)
            const snapshot = aggregator.getSnapshot()
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.agentEvents = [...snapshot.agentEvents]
            }
          },
          onAgentComplete(data) {
            aggregator.consume('agent_complete', data as Record<string, unknown>)
            const snapshot = aggregator.getSnapshot()
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.agentEvents = [...snapshot.agentEvents]
            }
          },
          onAgentThinking(data) {
            aggregator.consume('agent_thinking', data as Record<string, unknown>)
            const snapshot = aggregator.getSnapshot()
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.thinkingBlocks = [...snapshot.thinkingBlocks]
            }
          },
          onAgentToolStep(data) {
            aggregator.consume('agent_tool_step', data as Record<string, unknown>)
            const snapshot = aggregator.getSnapshot()
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.thinkingBlocks = [...snapshot.thinkingBlocks]
            }
          },
          onAgentThinkingDone(data) {
            aggregator.consume('agent_thinking_done', data as Record<string, unknown>)
            const snapshot = aggregator.getSnapshot()
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.thinkingBlocks = [...snapshot.thinkingBlocks]
            }
          },
          onAgentContentDelta(data) {
            if (isDone) return
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              const token = data.data?.token || ''
              msg.content = (msg.content || '') + token
            }
          },
          onAgentQuestionnaire(data) {
            if (isDone) return
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              const d = data.data || data
              // 同 id 问卷已渲染则不覆盖（防 resume 重放重复事件）；新 id 自然覆盖旧问卷
              if (msg.questionnaire?.questionnaire_id === (d.questionnaire_id as string)) return
              msg.questionnaire = {
                questionnaire_id: d.questionnaire_id as string,
                questions: (d.questionnaire_data?.questions || []) as never[],
              }
              msg.questionnaireError = undefined
            }
          },
          onAgentQuestionnaireCancelled(data) {
            const d = ((data as Record<string, unknown>).data || data) as Record<string, unknown>
            const qid = d.questionnaire_id as string
            if (qid) {
              const msg = messages.value.find((m) => m.questionnaire?.questionnaire_id === qid)
              if (msg) msg.questionnaire = undefined
            }
          },
          onSuggestions(data) {
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) msg.suggestions = data.suggestions
          },
          onDone(data) {
            isDone = true
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.citations = data.citations || []
              msg.assistantMessageId = data.assistant_message_id
              msg.traceId = data.trace_id
              msg.metadata = {
                swarmEnabled: data.swarm_enabled ?? false,
                agentsInvolved: data.agents_involved || [],
                totalTime: data.total_time,
                usage: data.usage,
                performanceMetrics: data.performance_metrics
                  ? {
                      parallelEfficiency: data.performance_metrics.parallel_efficiency || 0,
                      informationCoverage: data.performance_metrics.information_coverage || 0,
                      redundancy: data.performance_metrics.redundancy || 0,
                    }
                  : undefined,
              }
              // 兜底：折叠所有 thinking 块
              if (msg.thinkingBlocks) {
                msg.thinkingBlocks.forEach((b) => {
                  b.isCollapsed = true
                })
              }

              typewriter?.cancel()
              let typingCompleted = false
              const nextTypewriter = typeRemainingText({
                currentText: msg.content || '',
                targetText: data.answer || msg.content || '',
                onUpdate(text) {
                  msg.content = text
                },
                onComplete() {
                  typingCompleted = true
                  msg.isStreaming = false
                  isStreaming.value = false
                  typewriter = null
                },
              })
              typewriter = typingCompleted ? null : nextTypewriter
            } else {
              isStreaming.value = false
            }
          },
          onError(data) {
            typewriter?.cancel()
            typewriter = null
            error.value = data.error
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg) {
              msg.content = `错误：${data.error}`
              msg.isStreaming = false
            }
            isStreaming.value = false
          },
          onStreamEnd() {
            const msg = messages.value.find((m) => m.id === assistantMsg.id)
            if (msg && msg.isStreaming) {
              if (!msg.content) {
                msg.content = '请求已结束，但未收到完整响应。请重试。'
              }
              msg.isStreaming = false
              isStreaming.value = false
            }
          },
        },
      )
    } catch (e: unknown) {
      if (e instanceof Error && e.name === 'AbortError') return
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
      const msg = messages.value.find((m) => m.id === assistantMsg.id)
      if (msg) {
        msg.content = `请求失败：${message}`
        msg.isStreaming = false
      }
      isStreaming.value = false
    }
  }

  /** 从持久化的 SSE 事件列表重建 UI 状态 */
  function reconstructFromEvents(
    rawEvents: Array<{ event: string; data: Record<string, unknown> }>,
  ) {
    const SKIP_EVENT_TYPES = new Set([
      'agent_content_delta',
      'start',
      'done',
      'error',
      'suggestions',
    ])

    const aggregator = createEventAggregator(false)

    for (const item of rawEvents) {
      const eventType = item.event || ''
      if (SKIP_EVENT_TYPES.has(eventType)) continue
      aggregator.consume(eventType, item.data || {})
    }

    aggregator.finalize()
    return aggregator.getSnapshot()
  }

  async function loadHistory(sid: string) {
    try {
      const detail = await getSessionDetail(sid)
      if (!detail) return

      sessionId.value = detail.session_id
      sessionTitle.value = null
      messages.value = []

      if (detail.turns && detail.turns.length > 0) {
        for (const turn of detail.turns as SessionTurn[]) {
          if (turn.user_message?.content) {
            messages.value.push({
              id: genId(),
              role: 'user',
              content: turn.user_message.content,
              images: ((turn.user_message as Record<string, unknown>).images as string[]) || [],
              timestamp: turn.user_message.timestamp || new Date().toISOString(),
            })
          }
          if (turn.assistant_message?.content) {
            const rawEvents = (turn.assistant_message.agent_events || []) as Array<{
              event: string
              data: Record<string, unknown>
            }>
            const { agentEvents, thinkingBlocks, delegations } = reconstructFromEvents(rawEvents)
            const am = turn.assistant_message as Record<string, unknown>
            messages.value.push({
              id: genId(),
              role: 'assistant',
              content: am.content as string,
              assistantMessageId: am.assistant_message_id as string,
              traceId: am.trace_id as string,
              timestamp: (am.timestamp as string) || new Date().toISOString(),
              isStreaming: false,
              suggestions: (am.suggestions as string[]) || [],
              citations: (am.citations as ChatMessage['citations']) || [],
              agentEvents,
              thinkingBlocks,
              delegations,
              metadata: {
                swarmEnabled: am.mode === 'swarm',
                agentsInvolved: (am.agents_involved as string[]) || [],
                totalTime: am.total_time as number,
                subtasksCompleted: am.subtasks_completed as number,
                usage: { total_tokens: (detail.total_tokens as number) || 0 },
                performanceMetrics: {
                  parallelEfficiency: (detail.parallel_efficiency as number) || 0,
                  informationCoverage: (detail.information_coverage as number) || 0,
                  redundancy: (detail.redundancy as number) || 0,
                },
              },
            })
          }
        }
      } else {
        // 旧版单轮会话兼容逻辑
        if (detail.question) {
          messages.value.push({
            id: genId(),
            role: 'user',
            content: detail.question as string,
            timestamp: (detail.created_at as string) || new Date().toISOString(),
          })
        }
        if (detail.answer) {
          const rawEvents =
            (detail.agent_events as Array<{ event: string; data: Record<string, unknown> }>) || []
          const { agentEvents, thinkingBlocks } = reconstructFromEvents(rawEvents)

          messages.value.push({
            id: genId(),
            role: 'assistant',
            content: detail.answer as string,
            timestamp: (detail.created_at as string) || new Date().toISOString(),
            isStreaming: false,
            suggestions: (detail.suggestions as string[]) || [],
            agentEvents,
            thinkingBlocks,
            metadata: {
              swarmEnabled: detail.mode === 'swarm',
              agentsInvolved: (detail.agents_involved as string[]) || [],
              totalTime: detail.total_time as number,
              subtasksCompleted: detail.subtasks_completed as number,
              usage: { total_tokens: (detail.total_tokens as number) || 0 },
              performanceMetrics: {
                parallelEfficiency: (detail.parallel_efficiency as number) || 0,
                informationCoverage: (detail.information_coverage as number) || 0,
                redundancy: (detail.redundancy as number) || 0,
              },
            },
          })
        }
      }
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      error.value = `加载历史会话失败：${message}`
    }
  }

  function clearChat() {
    typewriter?.cancel()
    typewriter = null
    messages.value = []
    sessionId.value = null
    sessionTitle.value = null
    error.value = null
    disconnect()
    isStreaming.value = false
  }

  async function submitAnswers(questionnaireId: string, answers: Record<string, unknown>) {
    const msg = messages.value.find((m) => m.questionnaire?.questionnaire_id === questionnaireId)
    try {
      const resp = await fetch('/api/chat/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          questionnaire_id: questionnaireId,
          answers,
          session_id: sessionId.value,
        }),
      })
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`)
      }
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e)
      // 失败时保留问卷卡片并置错误态，供用户重试（避免后端一直等答案、SSE 挂起导致卡死）
      if (msg) {
        msg.questionnaireError = `提交答案失败：${message}，请重试`
      }
      error.value = `提交答案失败：${message}`
      return
    }

    // 仅成功时清空卡片；keyed 查找防 Q2 到达后 Q1 迟到 clear 误删
    const fresh = messages.value.find((m) => m.questionnaire?.questionnaire_id === questionnaireId)
    if (fresh) {
      fresh.questionnaire = undefined
      fresh.questionnaireError = undefined
    }
  }

  return {
    sessionId,
    sessionTitle,
    messages,
    isStreaming,
    error,
    sendMessage,
    loadHistory,
    clearChat,
    submitAnswers,
  }
})
