import { afterEach, describe, expect, it, vi } from 'vitest'

import { typeRemainingText } from '../typewriter'

describe('typeRemainingText', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('在已流式内容后逐步补齐最终答案', () => {
    vi.useFakeTimers()
    const updates: string[] = []
    const onComplete = vi.fn()

    typeRemainingText({
      currentText: '你好',
      targetText: '你好，世界',
      chunkSize: 2,
      intervalMs: 10,
      onUpdate: (text) => updates.push(text),
      onComplete,
    })

    vi.advanceTimersByTime(10)
    expect(updates).toEqual(['你好，世'])
    expect(onComplete).not.toHaveBeenCalled()

    vi.advanceTimersByTime(10)
    expect(updates).toEqual(['你好，世', '你好，世界'])
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('取消后不再更新内容', () => {
    vi.useFakeTimers()
    const onUpdate = vi.fn()
    const onComplete = vi.fn()
    const controller = typeRemainingText({
      currentText: '',
      targetText: '最终答案',
      onUpdate,
      onComplete,
    })

    controller.cancel()
    vi.runAllTimers()

    expect(onUpdate).not.toHaveBeenCalled()
    expect(onComplete).not.toHaveBeenCalled()
  })
})
