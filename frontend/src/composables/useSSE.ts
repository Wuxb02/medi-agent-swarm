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
  let currentRunId: string | null = null

  async function connect(_url: string, body: object, callbacks: StreamCallbacks) {
    controller?.abort()
    const connection = new AbortController()
    controller = connection

    let run: { run_id: string; session_id: string } | null = null
    const savedRun = sessionStorage.getItem('medizj.activeRun')
    if (savedRun) {
      const response = await fetch(`/api/chat/runs/${savedRun}`, { signal: connection.signal })
      if (response.ok) {
        const existing = await response.json()
        const input = body as { session_id?: string; question?: string }
        if (
          (!input.session_id || existing.session_id === input.session_id) &&
          existing.request.question === input.question
        ) {
          run = existing
        }
      } else if (response.status === 404) {
        sessionStorage.removeItem('medizj.activeRun')
      } else {
        throw new Error(`Request failed: ${response.status}`)
      }
    }
    if (!run) {
      const serialized = JSON.stringify(body)
      const pending = sessionStorage.getItem('medizj.pendingRun')
      const previous = pending ? JSON.parse(pending) : null
      const key = previous?.body === serialized ? previous.key : crypto.randomUUID()
      sessionStorage.setItem('medizj.pendingRun', JSON.stringify({ body: serialized, key }))
      const created = await fetch('/api/chat/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify(body),
        signal: connection.signal,
      })
      if (!created.ok) throw new Error(`Request failed: ${created.status}`)
      run = await created.json()
      sessionStorage.removeItem('medizj.pendingRun')
    }
    if (!run) throw new Error('Missing run response')
    sessionStorage.setItem('medizj.activeRun', run.run_id)
    currentRunId = run.run_id
    callbacks.onStart?.({ session_id: run.session_id } as StartData)
    let cursor = 0
    let complete = false
    while (!complete && !connection.signal.aborted) {
      let response: Response
      try {
        response = await fetch(`/api/chat/runs/${run.run_id}/events?after=${cursor}`, {
          signal: connection.signal,
        })
      } catch (error) {
        if (connection.signal.aborted) throw error
        await new Promise((resolve) => setTimeout(resolve, 1000))
        continue
      }
      if (!response.ok) throw new Error(`Request failed: ${response.status}`)
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
              if (msg.seq && msg.seq <= cursor) continue
              if (msg.seq) cursor = msg.seq
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
                  sessionStorage.removeItem('medizj.activeRun')
                  callbacks.onDone?.(data)
                  break
                case 'error':
                  complete = true
                  sessionStorage.removeItem('medizj.activeRun')
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
              sessionStorage.removeItem('medizj.activeRun')
              callbacks.onDone?.(msg.data)
            } else if (msg.event === 'error') {
              complete = true
              sessionStorage.removeItem('medizj.activeRun')
              callbacks.onError?.(msg.data)
            }
          } catch {
            /* ignore */
          }
        }

        // 如果没收到 done 事件，触发 fallback
        complete = complete || doneReceived
      } catch (error) {
        if (connection.signal.aborted) throw error
      } finally {
        reader.releaseLock()
      }
      if (!complete) await new Promise((resolve) => setTimeout(resolve, 1000))
    }
  }

  function disconnect() {
    controller?.abort()
    controller = null
  }

  async function cancel() {
    if (currentRunId) {
      const response = await fetch(`/api/chat/runs/${currentRunId}/cancel`, { method: 'POST' })
      if (!response.ok) throw new Error(`Request failed: ${response.status}`)
    }
    disconnect()
  }

  return { connect, disconnect, cancel, getRunId: () => currentRunId }
}
