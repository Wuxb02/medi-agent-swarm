/**
 * 统一的事件聚合器：将原始 SSE 事件转换为 UI 状态（AgentEvent / ThinkingBlock / TaskDelegation）。
 * 同时用于实时流式处理和历史回放重建，消除 chat.ts 中两套重复逻辑。
 */
import type {
  AgentEvent,
  ReasoningPhase,
  ReasoningStatus,
  ThinkingBlock,
  TaskDelegation,
} from '../types'
import { formatToolResult } from './formatToolResult'

let idCounter = 0
function genId(): string {
  return `evt-${Date.now()}-${++idCounter}`
}

/** 不需要展示的 Agent ID */
const SKIP_AGENTS = new Set(['swarm_coordinator'])

export interface AggregatorSnapshot {
  agentEvents: AgentEvent[]
  thinkingBlocks: ThinkingBlock[]
  delegations: TaskDelegation[]
}

export function createEventAggregator(isRealtime = false): {
  consume(eventType: string, data: Record<string, unknown>): void
  finalize(): void
  getSnapshot(): AggregatorSnapshot
} {
  const agentEvents: AgentEvent[] = []
  const thinkingBlocks: ThinkingBlock[] = []
  const delegations: TaskDelegation[] = []

  // 记录哪些 agent 有显式 thinking 事件（用于 finalize 补充摘要）
  const agentsWithThinking = new Set<string>()
  const thinkingAgents = new Set<string>()

  function consume(eventType: string, data: Record<string, unknown>) {
    switch (eventType) {
      case 'intent_classified': {
        const d = (data.data as Record<string, unknown>) || data
        const intent = d.intent === 'others' ? '非医疗对话' : '医疗咨询'
        const confidence = Number(d.confidence || 0)
        const route = d.intent === 'others' ? '进入直接回答' : '进入信息澄清与医疗分析'
        const reason = (d.reason as string) || '未提供额外理由'
        thinkingBlocks.push({
          id: (data.id as string) || genId(),
          agentId: 'lead_agent',
          thinking: `识别结果：${intent}\n置信度：${(confidence * 100).toFixed(0)}%\n判断理由：${reason}\n后续路由：${route}`,
          iteration: 0,
          phase: 'intent',
          title: '意图识别',
          status: 'completed',
          toolSteps: [],
          isCollapsed: !isRealtime,
        })
        break
      }

      case 'task_decomposed': {
        const inner = (data.data as Record<string, unknown>) || data
        agentEvents.push({
          id: (data.id as string) || genId(),
          type: 'decomposed',
          agentId: 'lead_agent',
          timestamp: (data.timestamp as string) || new Date().toISOString(),
          data: data as Record<string, unknown>,
        })
        if (inner && (inner.subtask_id || inner.type)) {
          delegations.push({
            subtaskId: (inner.subtask_id as string) || (inner.id as string) || '',
            type: (inner.type as string) || (inner.subtask_type as string) || '',
            description: (inner.description as string) || '',
            assignedAgent: (inner.assigned_agent as string) || 'unknown',
          })
        }
        break
      }

      case 'agent_start': {
        const agentId = (data.source_agent as string) || (data.agent_id as string) || 'unknown'
        const inner = (data.data as Record<string, unknown>) || data
        agentEvents.push({
          id: (data.id as string) || genId(),
          type: 'start',
          agentId,
          subtaskId: (inner.subtask_id as string) || undefined,
          subtaskType: (inner.subtask_type as string) || undefined,
          timestamp: (data.timestamp as string) || new Date().toISOString(),
          data: inner,
        })
        break
      }

      case 'agent_complete': {
        const agentId = (data.source_agent as string) || (data.agent_id as string) || 'unknown'
        agentEvents.push({
          id: (data.id as string) || genId(),
          type: 'complete',
          agentId,
          timestamp: (data.timestamp as string) || new Date().toISOString(),
          data: (data.data as Record<string, unknown>) || data,
        })
        break
      }

      case 'agent_thinking': {
        const d = (data.data as Record<string, unknown>) || data
        const agentId = (data.source_agent as string) || 'unknown'
        if (SKIP_AGENTS.has(agentId)) break

        const iteration = (d.iteration as number) || 0
        const phase = d.phase as ReasoningPhase | undefined
        agentsWithThinking.add(agentId)
        thinkingAgents.add(agentId)

        // 同 agent + 同 iteration 追加到已有 block
        const condition = isRealtime
          ? (b: ThinkingBlock) =>
              b.agentId === agentId &&
              b.iteration === iteration &&
              !b.isCollapsed &&
              b.phase === phase
          : (b: ThinkingBlock) =>
              b.agentId === agentId && b.iteration === iteration && b.phase === phase

        const existing = thinkingBlocks.findLast(condition)
        if (existing) {
          existing.thinking += (d.content as string) || ''
          existing.status = (d.status as ReasoningStatus) || existing.status
        } else {
          thinkingBlocks.push({
            id: genId(),
            agentId,
            thinking: (d.content as string) || '',
            iteration,
            phase,
            title: d.title as string | undefined,
            status: (d.status as ReasoningStatus) || 'running',
            toolSteps: [],
            isCollapsed: !isRealtime, // 实时流：展开；历史回放：折叠
          })
        }
        break
      }

      case 'agent_tool_step': {
        const d = (data.data as Record<string, unknown>) || data
        const iteration = (d.iteration as number) || 0
        const phase = d.phase as ReasoningPhase | undefined
        const agentId = (data.source_agent as string) || 'unknown'
        if (SKIP_AGENTS.has(agentId)) break
        thinkingAgents.add(agentId)

        // 两级兜底查找
        const block =
          thinkingBlocks.findLast(
            (b) => b.iteration === iteration && b.agentId === agentId && b.phase === phase,
          ) ||
          thinkingBlocks.findLast((b) => b.agentId === agentId) ||
          thinkingBlocks[thinkingBlocks.length - 1]

        if (block) {
          const toolName = (d.tool_name as string) || 'unknown'
          const toolArguments = (d.arguments as Record<string, unknown>) || {}
          const existingStep = block.toolSteps.findLast(
            (step) =>
              step.toolName === toolName &&
              step.status === 'waiting' &&
              step.arguments.round === toolArguments.round,
          )
          const nextStep = {
            toolName,
            arguments: toolArguments,
            result: formatToolResult(d.result),
            success: d.success !== false,
            status: d.status as ReasoningStatus | undefined,
          }
          if (existingStep) {
            Object.assign(existingStep, nextStep, {
              arguments: { ...existingStep.arguments, ...toolArguments },
            })
          } else {
            block.toolSteps.push(nextStep)
          }
          if (d.status) block.status = d.status as ReasoningStatus
        }
        break
      }

      case 'agent_thinking_done': {
        const d = (data.data as Record<string, unknown>) || data
        const iteration = (d.iteration as number) || 0
        const phase = d.phase as ReasoningPhase | undefined
        const agentId = (data.source_agent as string) || 'unknown'
        if (SKIP_AGENTS.has(agentId)) break

        // 两级兜底查找
        const block =
          thinkingBlocks.findLast(
            (b) => b.iteration === iteration && b.agentId === agentId && b.phase === phase,
          ) ||
          thinkingBlocks.findLast((b) => b.agentId === agentId) ||
          thinkingBlocks[thinkingBlocks.length - 1]

        if (block) {
          block.elapsedSeconds = d.elapsed_seconds as number | undefined
          block.status = (d.status as ReasoningStatus) || 'completed'
          block.isCollapsed = true
        }
        break
      }
    }
  }

  function finalize() {
    // 为有 thinking 但没有 timeline 的 Agent 补充 start/complete 事件
    for (const agentId of thinkingAgents) {
      const hasStart = agentEvents.some((e) => e.type === 'start' && e.agentId === agentId)
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
    for (const evt of agentEvents) {
      if (evt.type !== 'start' || agentsWithThinking.has(evt.agentId)) continue
      if (SKIP_AGENTS.has(evt.agentId)) continue

      const completeEvt = agentEvents.find(
        (e) => e.type === 'complete' && e.agentId === evt.agentId,
      )
      const execTime = completeEvt?.data?.execution_time as number | undefined
      const subtasks =
        (completeEvt?.data?.subtasks_completed as number) ?? (evt.data?.subtasks_count as number)
      const tools = evt.data?.tool_calls as number | undefined
      const summary = [
        subtasks ? `处理 ${subtasks} 个子任务` : '',
        tools ? `调用 ${tools} 次工具` : '',
        execTime ? `执行耗时 ${execTime}s` : '',
      ]
        .filter(Boolean)
        .join('，')

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

  function getSnapshot(): AggregatorSnapshot {
    return { agentEvents, thinkingBlocks, delegations }
  }

  return { consume, finalize, getSnapshot }
}
