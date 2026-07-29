/**
 * 格式化工具调用结果：处理 dict / stringified dict / JSON 等。
 */
export function formatToolResult(result: unknown): string {
  if (result == null) return ''
  if (typeof result === 'string') {
    // 尝试解析 JSON 字符串
    try {
      const parsed = JSON.parse(result)
      if (parsed && typeof parsed === 'object') {
        return parsed.answer || parsed.content || JSON.stringify(parsed, null, 2)
      }
    } catch {
      /* 不是 JSON，继续 */
    }
    // 尝试解析 Python dict 字符串: {'answer': '...'}
    const pyMatch = result.match(/^\{['"]answer['"]:\s*['"]([\s\S]*?)['"]\}$/)
    if (pyMatch) return pyMatch[1].replace(/\\n/g, '\n')
    return result
  }
  if (typeof result === 'object') {
    const obj = result as Record<string, unknown>
    return (obj.answer as string) || (obj.content as string) || JSON.stringify(obj, null, 2)
  }
  return String(result)
}
