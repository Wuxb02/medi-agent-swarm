export interface TypewriterController {
  cancel: () => void
}

interface TypewriterOptions {
  currentText: string
  targetText: string
  onUpdate: (text: string) => void
  onComplete: () => void
  chunkSize?: number
  intervalMs?: number
}

/**
 * 将最终答案中尚未收到的部分逐帧补齐。
 * 正常情况下正文由 SSE token 驱动，这里同时覆盖无 token 的降级路径。
 */
export function typeRemainingText(options: TypewriterOptions): TypewriterController {
  const chunkSize = options.chunkSize ?? 6
  const intervalMs = options.intervalMs ?? 16
  let displayedText = options.currentText
  let timer: ReturnType<typeof setTimeout> | undefined
  let cancelled = false

  if (!options.targetText.startsWith(displayedText)) {
    displayedText = ''
    options.onUpdate(displayedText)
  }

  const remainingChars = Array.from(options.targetText.slice(displayedText.length))
  let cursor = 0

  const complete = () => {
    if (!cancelled) options.onComplete()
  }

  const tick = () => {
    if (cancelled) return

    displayedText += remainingChars.slice(cursor, cursor + chunkSize).join('')
    cursor += chunkSize
    options.onUpdate(displayedText)

    if (cursor >= remainingChars.length) {
      complete()
      return
    }
    timer = setTimeout(tick, intervalMs)
  }

  if (remainingChars.length === 0) {
    complete()
  } else {
    timer = setTimeout(tick, intervalMs)
  }

  return {
    cancel() {
      cancelled = true
      if (timer) clearTimeout(timer)
    },
  }
}
