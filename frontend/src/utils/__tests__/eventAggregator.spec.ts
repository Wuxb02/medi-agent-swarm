import { describe, expect, it } from 'vitest'

import { createEventAggregator } from '../eventAggregator'

describe('LeadAgent 事件聚合', () => {
  it('将意图识别归入 LeadAgent 协调过程', () => {
    const aggregator = createEventAggregator(true)

    aggregator.consume('intent_classified', {
      source_agent: 'intent_classifier',
      data: {
        intent: 'medical',
        confidence: 0.92,
        reason: '存在明确症状咨询',
      },
    })

    const [block] = aggregator.getSnapshot().thinkingBlocks
    expect(block.agentId).toBe('lead_agent')
    expect(block.phase).toBe('intent')
    expect(block.thinking).toContain('医疗咨询')
    expect(block.thinking).toContain('92%')
  })

  it('按阶段区分相同迭代号并关联问卷工具', () => {
    const aggregator = createEventAggregator(true)
    const envelope = (data: Record<string, unknown>) => ({
      source_agent: 'lead_agent',
      data,
    })

    aggregator.consume(
      'agent_thinking',
      envelope({ content: '正在澄清', iteration: 1, phase: 'clarify' }),
    )
    aggregator.consume(
      'agent_tool_step',
      envelope({
        tool_name: 'question_for_user',
        arguments: { round: 1 },
        result: '等待用户回答',
        iteration: 1,
        phase: 'clarify',
        status: 'waiting',
      }),
    )
    aggregator.consume(
      'agent_tool_step',
      envelope({
        tool_name: 'question_for_user',
        arguments: { round: 1 },
        result: '您的年龄是？: 35',
        iteration: 1,
        phase: 'clarify',
        status: 'completed',
      }),
    )
    aggregator.consume(
      'agent_thinking',
      envelope({ content: '正在分解', iteration: 1, phase: 'decompose' }),
    )

    const blocks = aggregator.getSnapshot().thinkingBlocks
    expect(blocks).toHaveLength(2)
    expect(blocks[0].phase).toBe('clarify')
    expect(blocks[0].toolSteps).toHaveLength(1)
    expect(blocks[0].toolSteps[0].toolName).toBe('question_for_user')
    expect(blocks[0].toolSteps[0].result).toContain('35')
    expect(blocks[0].toolSteps[0].status).toBe('completed')
    expect(blocks[1].phase).toBe('decompose')
  })

  it('兼容无 phase 的历史思考事件', () => {
    const aggregator = createEventAggregator(false)
    aggregator.consume('agent_thinking', {
      source_agent: 'lead_agent',
      data: { content: '旧版任务分解', iteration: 1 },
    })

    const [block] = aggregator.getSnapshot().thinkingBlocks
    expect(block.phase).toBeUndefined()
    expect(block.thinking).toBe('旧版任务分解')
  })
})
