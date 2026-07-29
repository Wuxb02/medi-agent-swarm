import { describe, it, expect } from 'vitest'
import { formatToolResult } from '../../utils/formatToolResult'

describe('formatToolResult', () => {
  it('null/undefined 返回空字符串', () => {
    expect(formatToolResult(null)).toBe('')
    expect(formatToolResult(undefined)).toBe('')
  })

  it('普通字符串原样返回', () => {
    expect(formatToolResult('hello')).toBe('hello')
  })

  it('JSON 字符串提取 answer 字段', () => {
    expect(formatToolResult('{"answer": "测试回复"}')).toBe('测试回复')
  })

  it('JSON 字符串提取 content 字段（无 answer 时）', () => {
    expect(formatToolResult('{"content": "内容文本"}')).toBe('内容文本')
  })

  it('Python dict 字符串提取 answer', () => {
    expect(formatToolResult("{'answer': 'Python回复'}")).toBe('Python回复')
  })

  it('对象提取 answer 字段', () => {
    expect(formatToolResult({ answer: '对象回复' })).toBe('对象回复')
  })

  it('非 JSON 字符串原样返回', () => {
    expect(formatToolResult('not a json string')).toBe('not a json string')
  })
})
