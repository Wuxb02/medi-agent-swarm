import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSSE } from '../useSSE'
import type { StreamCallbacks } from '../useSSE'

function stream(lines: string, terminated = true) {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(lines))
        if (terminated) controller.close()
      },
    }),
  )
}

beforeEach(() => sessionStorage.clear())
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('持久化事件订阅', () => {
  it('分发全部事件类型并跳过无效行', async () => {
    const callbacks: StreamCallbacks = {
      onStart: vi.fn(),
      onTaskDecomposed: vi.fn(),
      onAgentStart: vi.fn(),
      onAgentToolCall: vi.fn(),
      onAgentToolResult: vi.fn(),
      onAgentComplete: vi.fn(),
      onAgentThinking: vi.fn(),
      onAgentToolStep: vi.fn(),
      onAgentThinkingDone: vi.fn(),
      onAgentContentDelta: vi.fn(),
      onAgentQuestionnaire: vi.fn(),
      onAgentQuestionnaireCancelled: vi.fn(),
      onIntentClassified: vi.fn(),
      onTraceSpan: vi.fn(),
      onSuggestions: vi.fn(),
      onDone: vi.fn(),
    }
    const events = [
      'start',
      'task_decomposed',
      'agent_start',
      'agent_tool_call',
      'agent_tool_result',
      'agent_complete',
      'agent_thinking',
      'agent_tool_step',
      'agent_thinking_done',
      'agent_content_delta',
      'agent_questionnaire',
      'agent_questionnaire_cancelled',
      'intent_classified',
      'trace_span',
      'suggestions',
      'done',
    ]
    const lines = events
      .map((event, index) => JSON.stringify({ seq: index + 1, event, data: {} }))
      .join('\n')
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(Response.json({ run_id: 'run', session_id: 'session' }))
        .mockResolvedValueOnce(stream(`\ninvalid JSON\n${lines}\n`)),
    )
    await useSSE().connect('', { question: '问题' }, callbacks)
    for (const callback of Object.values(callbacks)) expect(callback).toHaveBeenCalled()
  })

  it.each(['create', 'lookup', 'events'])('%s 的 HTTP 故障明确上报', async (stage) => {
    const fetch = vi.fn()
    if (stage === 'lookup') {
      sessionStorage.setItem('medizj.activeRun', 'run')
      fetch.mockResolvedValueOnce(new Response('', { status: 503 }))
    } else if (stage === 'create') {
      fetch.mockResolvedValueOnce(new Response('', { status: 503 }))
    } else {
      fetch
        .mockResolvedValueOnce(Response.json({ run_id: 'run', session_id: 'session' }))
        .mockResolvedValueOnce(new Response('', { status: 503 }))
    }
    vi.stubGlobal('fetch', fetch)
    await expect(useSSE().connect('', { question: '问题' }, {})).rejects.toThrow('503')
  })

  it('已删除或不匹配的执行创建新任务', async () => {
    for (const existing of [
      new Response('', { status: 404 }),
      Response.json({
        run_id: 'old',
        session_id: 'other',
        request: { question: '其他问题' },
      }),
    ]) {
      sessionStorage.setItem('medizj.activeRun', 'old')
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(existing)
        .mockResolvedValueOnce(Response.json({ run_id: 'new', session_id: 'session' }))
        .mockResolvedValueOnce(stream('{"seq":1,"event":"error","data":{"error":"结束"}}\n'))
      vi.stubGlobal('fetch', fetch)
      const error = vi.fn()
      await useSSE().connect('', { question: '问题', session_id: 'session' }, { onError: error })
      expect(fetch.mock.calls[1][0]).toBe('/api/chat/runs')
      expect(error).toHaveBeenCalledOnce()
    }
  })

  it('网络及读取断线后按已确认序号恢复并释放读取锁', async () => {
    vi.useFakeTimers()
    const reader = {
      releaseLock: vi.fn(),
      read: vi
        .fn()
        .mockResolvedValueOnce({
          done: false,
          value: new TextEncoder().encode('{"seq":1,"event":"agent_start","data":{}}\n'),
        })
        .mockRejectedValueOnce(new Error('读取断线')),
    }
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ run_id: 'run', session_id: 'session' }))
      .mockRejectedValueOnce(new TypeError('网络断线'))
      .mockResolvedValueOnce({ ok: true, body: { getReader: () => reader } })
      .mockResolvedValueOnce(stream('{"seq":2,"event":"done","data":{}}\n'))
    vi.stubGlobal('fetch', fetch)
    const pending = useSSE().connect('', { question: '问题' }, {})
    await vi.runAllTimersAsync()
    await pending
    expect(fetch.mock.calls[3][0]).toBe('/api/chat/runs/run/events?after=1')
    expect(reader.releaseLock).toHaveBeenCalledOnce()
  })

  it('断线后按序号补读，重复事件仅消费一次', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ run_id: 'run', session_id: 'session' }))
      .mockResolvedValueOnce(
        stream('{"seq":1,"event":"agent_content_delta","data":{"token":"一"}}\n'),
      )
      .mockResolvedValueOnce(
        stream(
          '{"seq":1,"event":"agent_content_delta","data":{"token":"一"}}\n{"seq":2,"event":"done","data":{"answer":"完成"}}',
        ),
      )
    vi.stubGlobal('fetch', fetch)
    const content = vi.fn()
    const done = vi.fn()
    await useSSE().connect('', { question: '问题' }, { onAgentContentDelta: content, onDone: done })
    expect(fetch.mock.calls[2][0]).toBe('/api/chat/runs/run/events?after=1')
    expect(content).toHaveBeenCalledTimes(1)
    expect(done).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('medizj.activeRun')).toBeNull()
  })

  it('刷新后恢复已有执行，不重复创建任务', async () => {
    sessionStorage.setItem('medizj.activeRun', 'run')
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ run_id: 'run', session_id: 'session', request: { question: '问题' } }),
      )
      .mockResolvedValueOnce(stream('{"seq":1,"event":"done","data":{}}\n'))
    vi.stubGlobal('fetch', fetch)
    await useSSE().connect('', { question: '问题' }, {})
    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      '/api/chat/runs/run',
      '/api/chat/runs/run/events?after=0',
    ])
  })

  it('创建响应丢失后保留同一幂等键', async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('网络中断'))
      .mockResolvedValueOnce(Response.json({ run_id: 'run', session_id: 'session' }))
      .mockResolvedValueOnce(stream('{"seq":1,"event":"error","data":{"message":"失败"}}'))
    vi.stubGlobal('fetch', fetch)
    const client = useSSE()
    await expect(client.connect('', { question: '问题' }, {})).rejects.toThrow('网络中断')
    await client.connect('', { question: '问题' }, {})
    expect(fetch.mock.calls[0][1].headers['Idempotency-Key']).toBe(
      fetch.mock.calls[1][1].headers['Idempotency-Key'],
    )
    expect(sessionStorage.getItem('medizj.activeRun')).toBeNull()
  })

  it('断开连接中止订阅，显式取消才调用取消接口', async () => {
    const client = useSSE()
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ run_id: 'run', session_id: 'session' }))
      .mockImplementationOnce(
        (_url, options) =>
          new Promise((_resolve, reject) => {
            options.signal.addEventListener('abort', () =>
              reject(new DOMException('中止', 'AbortError')),
            )
          }),
      )
      .mockResolvedValueOnce(Response.json({ status: 'cancelled' }))
    vi.stubGlobal('fetch', fetch)
    const pending = client.connect('', { question: '问题' }, {})
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    client.disconnect()
    await expect(pending).rejects.toThrow('中止')
    expect(fetch).toHaveBeenCalledTimes(2)
    await client.cancel()
    expect(fetch.mock.calls[2][0]).toBe('/api/chat/runs/run/cancel')
  })
})
