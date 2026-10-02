import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { StreamCallbacks } from '../../composables/useSSE'
import { useChatStore } from '../chat'

const transport = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  getRunId: vi.fn(() => 'run'),
}))
const history = vi.hoisted(() => vi.fn())
vi.mock('../../composables/useSSE', () => ({ useSSE: () => transport }))
vi.mock('../../api/session', () => ({ getSessionDetail: history }))
vi.mock('../../utils/typewriter', () => ({
  typeRemainingText(options: {
    targetText: string
    onUpdate: (text: string) => void
    onComplete: () => void
  }) {
    options.onUpdate(options.targetText)
    options.onComplete()
    return { cancel: vi.fn() }
  },
}))

let callbacks: StreamCallbacks
function emit(name: keyof StreamCallbacks, data: Record<string, unknown> = {}) {
  const callback = callbacks[name] as ((value: Record<string, unknown>) => void) | undefined
  callback?.(data)
}

beforeEach(() => {
  sessionStorage.clear()
  setActivePinia(createPinia())
  history.mockReset()
  transport.connect
    .mockReset()
    .mockImplementation(async (_url, _body, handlers: StreamCallbacks) => {
      callbacks = handlers
    })
  transport.disconnect.mockClear()
})
afterEach(() => vi.unstubAllGlobals())

describe('聊天执行状态与恢复', () => {
  it('聚合真实回调，保留终态引用、消息标识与问卷去重', async () => {
    const store = useChatStore()
    await store.sendMessage('问题', ['image'])
    await store.sendMessage('重复点击')
    expect(transport.connect).toHaveBeenCalledTimes(1)
    expect(store.messages[0].images).toEqual(['image'])
    emit('onStart', { session_id: 'session' })
    emit('onIntentClassified', { data: { intent: 'medical' } })
    emit('onTaskDecomposed', {
      data: { subtask_id: 'task', type: 'consultation', assigned_agent: 'agent' },
    })
    emit('onAgentStart', { source_agent: 'agent', data: { subtask_id: 'task' } })
    emit('onAgentThinking', { source_agent: 'agent', data: { content: '核验来源', iteration: 1 } })
    emit('onAgentToolStep', { source_agent: 'agent', data: { tool_name: 'search', iteration: 1 } })
    emit('onAgentThinkingDone', { source_agent: 'agent', data: { iteration: 1 } })
    emit('onAgentComplete', { source_agent: 'agent', data: {} })
    emit('onAgentContentDelta', { data: { token: '建议' } })
    emit('onAgentQuestionnaire', {
      questionnaire_id: 'q1',
      questionnaire_data: { questions: [{ id: 'one' }] },
    })
    emit('onAgentQuestionnaire', {
      data: { questionnaire_id: 'q1', questionnaire_data: { questions: [] } },
    })
    expect(store.messages[1].questionnaire?.questions).toEqual([{ id: 'one' }])
    emit('onAgentQuestionnaireCancelled', { data: { questionnaire_id: 'q1' } })
    expect(store.messages[1].questionnaire).toBeUndefined()
    emit('onSuggestions', { suggestions: ['复诊'] })
    emit('onDone', {
      answer: '建议复诊',
      assistant_message_id: '7',
      trace_id: 'trace',
      citations: [{ index: 1 }],
      swarm_enabled: true,
      agents_involved: ['agent'],
      performance_metrics: { parallel_efficiency: 0.5, information_coverage: 0.8, redundancy: 0.1 },
    })
    emit('onAgentContentDelta', { data: { token: '迟到内容' } })
    emit('onAgentQuestionnaire', { questionnaire_id: 'late' })
    expect(store.sessionId).toBe('session')
    expect(store.messages[1].content).toBe('建议复诊')
    expect(store.messages[1].assistantMessageId).toBe('7')
    expect(store.messages[1].citations).toEqual([{ index: 1 }])
    expect(store.messages[1].metadata?.performanceMetrics?.parallelEfficiency).toBe(0.5)
    expect(store.messages[1].thinkingBlocks?.every((block) => block.isCollapsed)).toBe(true)
    expect(store.isStreaming).toBe(false)
    store.clearChat()
    expect(store.messages).toEqual([])
    expect(store.sessionId).toBeNull()
    expect(transport.disconnect).toHaveBeenCalledOnce()
  })

  it('空请求不创建执行，流结束或错误释放界面状态', async () => {
    const store = useChatStore()
    await store.sendMessage('  ')
    expect(transport.connect).not.toHaveBeenCalled()
    await store.sendMessage('问题')
    emit('onStreamEnd')
    expect(store.messages[1].content).toContain('未收到完整响应')
    expect(store.isStreaming).toBe(false)
    await store.sendMessage('再试')
    emit('onError', { error: '服务故障' })
    expect(store.error).toBe('服务故障')
    expect(store.messages.at(-1)?.isStreaming).toBe(false)
  })

  it('网络失败可再次发送，已移除的消息不会阻塞终态', async () => {
    const store = useChatStore()
    transport.connect.mockRejectedValueOnce(new Error('网络故障'))
    await store.sendMessage('问题')
    expect(store.error).toBe('网络故障')
    expect(store.isStreaming).toBe(false)
    await store.sendMessage('再试')
    store.messages = []
    emit('onDone', { answer: '结束' })
    expect(store.isStreaming).toBe(false)
  })

  it('答案包含执行标识，失败保留问卷，迟到确认不会删除下一份问卷', async () => {
    const store = useChatStore()
    await store.sendMessage('问题')
    emit('onAgentQuestionnaire', { questionnaire_id: 'q1' })
    const fetch = vi.fn().mockResolvedValueOnce(new Response('', { status: 409 }))
    vi.stubGlobal('fetch', fetch)
    await store.submitAnswers('q1', { symptom: '头痛' })
    expect(JSON.parse(fetch.mock.calls[0][1].body).run_id).toBe('run')
    expect(store.messages[1].questionnaire?.questionnaire_id).toBe('q1')
    expect(store.messages[1].questionnaireError).toContain('409')
    let confirm!: (value: Response) => void
    fetch.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          confirm = resolve
        }),
    )
    const submission = store.submitAnswers('q1', { symptom: '头痛' })
    emit('onAgentQuestionnaire', { data: { questionnaire_id: 'q2' } })
    confirm(new Response())
    await submission
    expect(store.messages[1].questionnaire?.questionnaire_id).toBe('q2')
    fetch.mockResolvedValueOnce(new Response())
    await store.submitAnswers('q2', {})
    expect(store.messages[1].questionnaire).toBeUndefined()
  })

  it('从已确认历史重建轮次、引用与事件，加载失败保留错误', async () => {
    history.mockResolvedValueOnce({
      session_id: 'session',
      total_tokens: 20,
      turns: [
        {
          user_message: { content: '问题', images: ['image'] },
          assistant_message: {
            content: '回答',
            assistant_message_id: '7',
            trace_id: 'trace',
            mode: 'swarm',
            citations: [{ index: 1 }],
            agent_events: [
              { event: 'start', data: {} },
              { event: 'agent_content_delta', data: { token: '重复' } },
              { event: 'agent_start', data: { source_agent: 'agent' } },
              { event: 'agent_complete', data: { source_agent: 'agent' } },
            ],
          },
        },
      ],
    })
    const store = useChatStore()
    await store.loadHistory('session')
    expect(store.messages.map((message) => message.content)).toEqual(['问题', '回答'])
    expect(store.messages[1].assistantMessageId).toBe('7')
    expect(store.messages[1].agentEvents).toHaveLength(2)
    expect(store.messages[1].metadata?.usage?.total_tokens).toBe(20)
    history.mockResolvedValueOnce({ session_id: 'legacy', question: '旧问题', answer: '旧回答' })
    await store.loadHistory('legacy')
    expect(store.messages.map((message) => message.content)).toEqual(['旧问题', '旧回答'])
    history.mockRejectedValueOnce(new Error('权限错误'))
    await store.loadHistory('denied')
    expect(store.error).toContain('权限错误')
  })

  it.each(['completed', 'waiting_answer'])('刷新后恢复 %s 执行', async (status) => {
    sessionStorage.setItem('medizj.activeRun', 'run')
    history.mockResolvedValue({ session_id: 'session', question: '问题', answer: '已保存回答' })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        Response.json({
          session_id: 'session',
          status,
          request: { question: '问题', images: [] },
        }),
      ),
    )
    const store = useChatStore()
    await vi.waitFor(() => expect(store.messages).toHaveLength(2))
    expect(store.sessionId).toBe('session')
    if (status === 'completed') {
      expect(store.messages[1].content).toBe('已保存回答')
      expect(sessionStorage.getItem('medizj.activeRun')).toBeNull()
    } else {
      expect(transport.connect).toHaveBeenCalledOnce()
    }
  })

  it('不存在的执行清除恢复标识，网络故障明确显示', async () => {
    sessionStorage.setItem('medizj.activeRun', 'missing')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 404 })))
    useChatStore()
    await vi.waitFor(() => expect(sessionStorage.getItem('medizj.activeRun')).toBeNull())
    setActivePinia(createPinia())
    sessionStorage.setItem('medizj.activeRun', 'run')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('离线')))
    const store = useChatStore()
    await vi.waitFor(() => expect(store.error).toBe('离线'))
  })
})
