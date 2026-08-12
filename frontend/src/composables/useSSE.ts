/**
 * 流式响应 composable（fetch + ReadableStream，换行分隔 JSON）
 */
import type {
  StartData,
  TaskDecomposedData,
  AgentStartData,
  AgentCompleteData,
  AgentThinkingData,
  AgentToolStepData,
  AgentThinkingDoneData,
  AgentContentDeltaData,
  AgentQuestionnaireData,
  IntentClassifiedData,
  DoneData,
  ErrorData,
} from '../types'

export interface StreamCallbacks {
  onStart?: (data: StartData) => void
  onTaskDecomposed?: (data: TaskDecomposedData) => void
  onAgentStart?: (data: AgentStartData) => void
  onAgentToolCall?: (data: Record<string, unknown>) => void
  onAgentToolResult?: (data: Record<string, unknown>) => void
  onAgentComplete?: (data: AgentCompleteData) => void
  onAgentThinking?: (data: AgentThinkingData) => void
  onAgentToolStep?: (data: AgentToolStepData) => void
  onAgentThinkingDone?: (data: AgentThinkingDoneData) => void
  onAgentContentDelta?: (data: AgentContentDeltaData) => void
  onAgentQuestionnaire?: (data: AgentQuestionnaireData) => void
  onAgentQuestionnaireCancelled?: (data: Record<string, unknown>) => void
  onIntentClassified?: (data: IntentClassifiedData) => void
  onTraceSpan?: (data: Record<string, unknown>) => void
  onSuggestions?: (data: { suggestions: string[] }) => void
  onDone?: (data: DoneData) => void
  onError?: (data: ErrorData) => void
  onStreamEnd?: () => void
}

export function useSSE() {
  let controller: AbortController | null = null

  async function connect(url: string, body: object, callbacks: StreamCallbacks) {
    controller = new AbortController()

    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`)
    }

    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let doneReceived = false

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed) continue

          try {
            const msg = JSON.parse(trimmed)
            const { event, data } = msg

            switch (event) {
              case 'start':
                callbacks.onStart?.(data)
                break
              case 'task_decomposed':
                callbacks.onTaskDecomposed?.(data)
                break
              case 'agent_start':
                callbacks.onAgentStart?.(data)
                break
              case 'agent_tool_call':
                callbacks.onAgentToolCall?.(data)
                break
              case 'agent_tool_result':
                callbacks.onAgentToolResult?.(data)
                break
              case 'agent_complete':
                callbacks.onAgentComplete?.(data)
                break
              case 'agent_thinking':
                callbacks.onAgentThinking?.(data)
                break
              case 'agent_tool_step':
                callbacks.onAgentToolStep?.(data)
                break
              case 'agent_thinking_done':
                callbacks.onAgentThinkingDone?.(data)
                break
              case 'agent_content_delta':
                callbacks.onAgentContentDelta?.(data)
                break
              case 'agent_questionnaire':
                callbacks.onAgentQuestionnaire?.(data)
                break
              case 'agent_questionnaire_cancelled':
                callbacks.onAgentQuestionnaireCancelled?.(data)
                break
              case 'intent_classified':
                callbacks.onIntentClassified?.(data)
                break
              case 'trace_span':
                callbacks.onTraceSpan?.(data)
                break
              case 'suggestions':
                callbacks.onSuggestions?.(data)
                break
              case 'done':
                doneReceived = true
                callbacks.onDone?.(data)
                break
              case 'error':
                callbacks.onError?.(data)
                break
            }
          } catch {
            // 跳过解析失败的行
          }
        }
      }

      // 处理 buffer 残留
      if (buffer.trim()) {
        try {
          const msg = JSON.parse(buffer.trim())
          if (msg.event === 'done') {
            doneReceived = true
            callbacks.onDone?.(msg.data)
          } else if (msg.event === 'error') {
            callbacks.onError?.(msg.data)
          }
        } catch {
          /* ignore */
        }
      }

      // 如果没收到 done 事件，触发 fallback
      if (!doneReceived) {
        callbacks.onStreamEnd?.()
      }
    } finally {
      reader.releaseLock()
    }
  }

  function disconnect() {
    controller?.abort()
    controller = null
  }

  return { connect, disconnect }
}
