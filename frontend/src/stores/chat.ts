import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, ThinkingBlock, AgentEvent, SessionTurn } from '../types'
import { useSSE } from '../composables/useSSE'
import { getSessionDetail } from '../api/session'

let msgIdCounter = 0
function genId() {
  return `msg-${Date.now()}-${++msgIdCounter}`
}

/** 格式化工具结果：处理 dict / stringified dict */
function formatToolResult(result: any): string {
  if (result == null) return ''
  if (typeof result === 'string') {
    // 尝试解析 JSON 字符串
    try {
      const parsed = JSON.parse(result)
      if (parsed && typeof parsed === 'object') {
        return parsed.answer || parsed.content || JSON.stringify(parsed, null, 2)
      }
    } catch { /* 不是 JSON，继续 */ }
    // 尝试解析 Python dict 字符串: {'answer': '...'}
    const pyMatch = result.match(/^\{['"]answer['"]:\s*['"]([\s\S]*?)['"]\}$/)
    if (pyMatch) return pyMatch[1].replace(/\\n/g, '\n')
    return result
  }
  if (typeof result === 'object') {
    return result.answer || result.content || JSON.stringify(result, null, 2)
  }
  return String(result)
}

/** 不需要在历史回放中显示的事件类型 */
const SKIP_EVENT_TYPES = new Set([
  'agent_content_delta', 'start', 'done', 'error', 'suggestions'
])

/** 从持久化的 SSE 事件列表重建 agentEvents 和 thinkingBlocks */
function reconstructFromEvents(rawEvents: any[]): {
  agentEvents: AgentEvent[]
  thinkingBlocks: ThinkingBlock[]
} {
  const agentEvents: AgentEvent[] = []
  const thinkingBlocks: ThinkingBlock[] = []

  // 记录哪些 agent 有显式 thinking 事件
  const agentsWithThinking = new Set<string>()
  // 收集 thinking 事件中出现的 agent（用于补充 timeline）
  const thinkingAgents = new Set<string>()

  for (const item of rawEvents) {
    const eventType: string = item.event || ''
    const data = item.data || {}

    // 跳过流式内容和控制事件
    if (SKIP_EVENT_TYPES.has(eventType)) continue

    switch (eventType) {
      case 'task_decomposed':
        agentEvents.push({
          id: data.id || genId(),
          type: 'decomposed',
          agentId: 'lead_agent',
          timestamp: data.timestamp || new Date().toISOString(),
          data,
        })
        break

      case 'agent_start': {
        const agentId = data.source_agent || 'unknown'
        agentEvents.push({
          id: data.id || genId(),
          type: 'start',
          agentId,
          subtaskId: data.data?.subtask_id,
          subtaskType: data.data?.subtask_type,
          timestamp: data.timestamp || new Date().toISOString(),
          data: data.data || data,
        })
        break
      }

      case 'agent_complete':
        agentEvents.push({
          id: data.id || genId(),
          type: 'complete',
          agentId: data.source_agent || 'unknown',
          timestamp: data.timestamp || new Date().toISOString(),
          data: data.data || data,
        })
        break

      case 'agent_thinking': {
        const d = data.data || data
        const agentId = data.source_agent || 'unknown'
        // swarm_coordinator 不展示思考过程
        if (agentId === 'swarm_coordinator') break
        const iteration = d.iteration || 0
        agentsWithThinking.add(agentId)
        thinkingAgents.add(agentId)
        // 同 agent + 同 iteration 追加到已有 block，避免拆成多个
        const existing = thinkingBlocks.findLast(
          b => b.agentId === agentId && b.iteration === iteration
        )
        if (existing) {
          existing.thinking += d.content || ''
        } else {
          thinkingBlocks.push({
            id: genId(),
            agentId,
            thinking: d.content || '',
            iteration,
            toolSteps: [],
            isCollapsed: true,
          })
        }
        break
      }

      case 'agent_tool_step': {
        const d = data.data || data
        const iteration = d.iteration || 0
        const sourceAgent = data.source_agent || 'unknown'
        thinkingAgents.add(sourceAgent)
        const block = thinkingBlocks.findLast(
          b => b.iteration === iteration && b.agentId === sourceAgent
        ) || thinkingBlocks.findLast(b => b.agentId === sourceAgent)
          || thinkingBlocks[thinkingBlocks.length - 1]
        if (block) {
          block.toolSteps.push({
            toolName: d.tool_name || 'unknown',
            arguments: d.arguments || {},
            result: formatToolResult(d.result),
            success: d.success !== false,
          })
        }
        break
      }

      case 'agent_thinking_done': {
        const d = data.data || data
        const iteration = d.iteration || 0
        const sourceAgent = data.source_agent || 'unknown'
        const block = thinkingBlocks.findLast(
          b => b.iteration === iteration && b.agentId === sourceAgent
        ) || thinkingBlocks.findLast(b => b.agentId === sourceAgent)
          || thinkingBlocks[thinkingBlocks.length - 1]
        if (block) {
          block.elapsedSeconds = d.elapsed_seconds
          block.isCollapsed = true
        }
        break
      }
    }
  }

  // 为有 thinking 但没有 timeline 的 Agent 补充 timeline 事件
  for (const agentId of thinkingAgents) {
    const hasStart = agentEvents.some(e => e.type === 'start' && e.agentId === agentId)
    if (!hasStart) {
      agentEvents.push({
        id: genId(),
        type: 'start',
        agentId,
        timestamp: agentEvents[0]?.timestamp || new Date().toISOString(),
        data: {},
      })
      agentEvents.push({
        id: genId(),
        type: 'complete',
        agentId,
        timestamp: agentEvents[agentEvents.length - 1]?.timestamp || new Date().toISOString(),
        data: {},
      })
    }
  }

  // 为没有显式 thinking 的 Agent 补充摘要 thinking block
  // 跳过 swarm_coordinator（智能路由不需要展示迭代 0 摘要）
  for (const evt of agentEvents) {
    if (evt.type === 'start' && !agentsWithThinking.has(evt.agentId)) {
      if (evt.agentId === 'swarm_coordinator') continue
      const completeEvt = agentEvents.find(
        e => e.type === 'complete' && e.agentId === evt.agentId
      )
      const execTime = completeEvt?.data?.execution_time
      const subtasks = completeEvt?.data?.subtasks_completed ?? evt.data?.subtasks_count
      const tools = evt.data?.tool_calls
      const summary = [
        subtasks ? `处理 ${subtasks} 个子任务` : '',
        tools ? `调用 ${tools} 次工具` : '',
        execTime ? `执行耗时 ${execTime}s` : '',
      ].filter(Boolean).join('，')

      thinkingBlocks.push({
        id: genId(),
        agentId: evt.agentId,
        thinking: summary || '已执行完成',
        iteration: 0,
        toolSteps: [],
        elapsedSeconds: execTime,
        isCollapsed: true,
      })
    }
  }

  return { agentEvents, thinkingBlocks }
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
      thinkingBlocks: [],
      suggestions: [],
      disclaimer: '',
    }
    messages.value.push(assistantMsg)

    let isDone = false
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
        onAgentThinking(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg) {
            if (!msg.thinkingBlocks) msg.thinkingBlocks = []
            const agentId = data.source_agent || 'unknown'
            // swarm_coordinator 不展示思考过程
            if (agentId === 'swarm_coordinator') return
            const iteration = data.data?.iteration || 0
            // 查找同 agent + 同 iteration 的最后一个未折叠 block，追加内容而非创建新 block
            const existing = msg.thinkingBlocks.findLast(
              b => b.agentId === agentId && b.iteration === iteration && !b.isCollapsed
            )
            if (existing) {
              existing.thinking += data.data?.content || ''
            } else {
              msg.thinkingBlocks.push({
                id: `think-${Date.now()}`,
                agentId,
                thinking: data.data?.content || '',
                iteration,
                toolSteps: [],
                isCollapsed: false,
              })
            }
          }
        },
        onAgentToolStep(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg?.thinkingBlocks && msg.thinkingBlocks.length > 0) {
            if (data.source_agent === 'swarm_coordinator') return
            const d = data.data || data
            const iteration = d.iteration || 0
            const block = msg.thinkingBlocks.findLast(b => b.iteration === iteration && b.agentId === (data.source_agent || 'unknown'))
              || msg.thinkingBlocks[msg.thinkingBlocks.length - 1]
            block.toolSteps.push({
              toolName: d.tool_name || 'unknown',
              arguments: d.arguments || {},
              result: d.result || '',
              success: d.success !== false,
            })
          }
        },
        onAgentThinkingDone(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg?.thinkingBlocks && msg.thinkingBlocks.length > 0) {
            if (data.source_agent === 'swarm_coordinator') return
            const d = data.data || data
            const iteration = d.iteration || 0
            const block = msg.thinkingBlocks.findLast(b => b.iteration === iteration && b.agentId === (data.source_agent || 'unknown'))
              || msg.thinkingBlocks[msg.thinkingBlocks.length - 1]
            block.elapsedSeconds = d.elapsed_seconds
            block.isCollapsed = true
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
        onSuggestions(data) {
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg) msg.suggestions = data.suggestions
        },
        onDone(data) {
          isDone = true
          const msg = messages.value.find((m) => m.id === assistantMsg.id)
          if (msg) {
            msg.content = data.answer || msg.content
            msg.disclaimer = data.disclaimer || ''
            msg.isStreaming = false
            msg.metadata = {
              swarmEnabled: data.swarm_enabled,
              agentsInvolved: data.agents_involved || [],
              totalTime: data.total_time,
              usage: data.usage,
              performanceMetrics: data.performance_metrics ? {
                parallelEfficiency: data.performance_metrics.parallel_efficiency || 0,
                informationCoverage: data.performance_metrics.information_coverage || 0,
                redundancy: data.performance_metrics.redundancy || 0,
              } : undefined,
            }
            // 兜底：折叠所有 thinking 块
            if (msg.thinkingBlocks) {
              msg.thinkingBlocks.forEach(b => { b.isCollapsed = true })
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

  async function loadHistory(sid: string) {
    try {
      const detail = await getSessionDetail(sid)
      if (!detail) return

      sessionId.value = detail.session_id
      messages.value = []

      if (detail.turns && detail.turns.length > 0) {
        // 多轮会话：遍历所有 turns
        for (const turn of detail.turns as SessionTurn[]) {
          // 用户消息
          if (turn.user_message?.content) {
            messages.value.push({
              id: genId(),
              role: 'user',
              content: turn.user_message.content,
              timestamp: turn.user_message.timestamp || new Date().toISOString(),
            })
          }
          // 助手消息
          if (turn.assistant_message?.content) {
            const rawEvents = turn.assistant_message.agent_events || []
            const { agentEvents, thinkingBlocks } = reconstructFromEvents(rawEvents)
            const am = turn.assistant_message
            messages.value.push({
              id: genId(),
              role: 'assistant',
              content: am.content,
              timestamp: am.timestamp || new Date().toISOString(),
              isStreaming: false,
              suggestions: am.suggestions || [],
              disclaimer: am.disclaimer || '',
              agentEvents,
              thinkingBlocks,
              metadata: {
                swarmEnabled: am.mode === 'swarm',
                agentsInvolved: am.agents_involved || [],
                totalTime: am.total_time,
                subtasksCompleted: am.subtasks_completed,
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
            content: detail.question,
            timestamp: detail.created_at || new Date().toISOString(),
          })
        }
        if (detail.answer) {
          const rawEvents = detail.agent_events || []
          const { agentEvents, thinkingBlocks } = reconstructFromEvents(rawEvents)

          messages.value.push({
            id: genId(),
            role: 'assistant',
            content: detail.answer,
            timestamp: detail.created_at || new Date().toISOString(),
            isStreaming: false,
            suggestions: detail.suggestions || [],
            disclaimer: detail.disclaimer || '',
            agentEvents,
            thinkingBlocks,
            metadata: {
              swarmEnabled: detail.mode === 'swarm',
              agentsInvolved: detail.agents_involved || [],
              totalTime: detail.total_time,
              subtasksCompleted: detail.subtasks_completed,
              performanceMetrics: detail.parallel_efficiency > 0 ? {
                parallelEfficiency: detail.parallel_efficiency || 0,
                informationCoverage: detail.information_coverage || 0,
                redundancy: detail.redundancy || 0,
              } : undefined,
            },
          })
        }
      }
    } catch (e: any) {
      error.value = `加载历史会话失败：${e.message}`
    }
  }

  function clearChat() {
    messages.value = []
    sessionId.value = null
    error.value = null
    disconnect()
    isStreaming.value = false
  }

  return { sessionId, messages, isStreaming, swarmMode, error, sendMessage, loadHistory, clearChat }
})
