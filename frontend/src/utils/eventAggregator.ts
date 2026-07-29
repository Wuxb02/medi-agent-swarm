/**
 * 统一的事件聚合器：将原始 SSE 事件转换为 UI 状态（AgentEvent / ThinkingBlock / TaskDelegation）。
 * 同时用于实时流式处理和历史回放重建，消除 chat.ts 中两套重复逻辑。
 */
import type { AgentEvent, ThinkingBlock, TaskDelegation } from '../types'
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
        agentsWithThinking.add(agentId)
        thinkingAgents.add(agentId)

        // 同 agent + 同 iteration 追加到已有 block
        const condition = isRealtime
          ? (b: ThinkingBlock) =>
              b.agentId === agentId && b.iteration === iteration && !b.isCollapsed
          : (b: ThinkingBlock) => b.agentId === agentId && b.iteration === iteration

        const existing = thinkingBlocks.findLast(condition)
        if (existing) {
          existing.thinking += (d.content as string) || ''
        } else {
          thinkingBlocks.push({
            id: genId(),
            agentId,
            thinking: (d.content as string) || '',
            iteration,
            toolSteps: [],
            isCollapsed: !isRealtime, // 实时流：展开；历史回放：折叠
          })
        }
        break
      }

      case 'agent_tool_step': {
        const d = (data.data as Record<string, unknown>) || data
        const iteration = (d.iteration as number) || 0
        const agentId = (data.source_agent as string) || 'unknown'
        if (SKIP_AGENTS.has(agentId)) break
        thinkingAgents.add(agentId)

        // 两级兜底查找
        const block =
          thinkingBlocks.findLast((b) => b.iteration === iteration && b.agentId === agentId) ||
          thinkingBlocks.findLast((b) => b.agentId === agentId) ||
          thinkingBlocks[thinkingBlocks.length - 1]

        if (block) {
          block.toolSteps.push({
            toolName: (d.tool_name as string) || 'unknown',
            arguments: (d.arguments as Record<string, unknown>) || {},
            result: formatToolResult(d.result),
            success: d.success !== false,
          })
        }
        break
      }

      case 'agent_thinking_done': {
        const d = (data.data as Record<string, unknown>) || data
        const iteration = (d.iteration as number) || 0
        const agentId = (data.source_agent as string) || 'unknown'
        if (SKIP_AGENTS.has(agentId)) break

        // 两级兜底查找
        const block =
          thinkingBlocks.findLast((b) => b.iteration === iteration && b.agentId === agentId) ||
          thinkingBlocks.findLast((b) => b.agentId === agentId) ||
          thinkingBlocks[thinkingBlocks.length - 1]

        if (block) {
          block.elapsedSeconds = d.elapsed_seconds as number | undefined
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
