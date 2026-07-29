import { describe, it, expect } from 'vitest'
import { useMarkdown } from '../useMarkdown'

describe('useMarkdown', () => {
  const { render } = useMarkdown()

  it('应渲染基本 Markdown 为 HTML', () => {
    const html = render('**粗体**')
    expect(html).toContain('<strong>粗体</strong>')
  })

  it('应渲染标题', () => {
    const html = render('# 标题')
    expect(html).toContain('<h1>标题</h1>')
  })

  it('应在代码块中保护引用格式不被转换', () => {
    const html = render('```\nconst x = [1,2,3]\n```')
    expect(html).toContain('[1,2,3]')
    expect(html).not.toContain('citation-ref')
  })

  it('应将引用标注 [N] 转换为可点击上标', () => {
    const html = render('详见 [1] 和 [2,3]')
    expect(html).toContain('citation-ref')
    expect(html).toContain('data-refs="1"')
    expect(html).toContain('data-refs="2,3"')
  })

  it('应处理范围引用 [1-3]', () => {
    const html = render('详见 [1-3]')
    expect(html).toContain('citation-ref')
    expect(html).toContain('data-refs="1,2,3"')
  })

  it('应过滤危险 HTML', () => {
    const html = render('<script>alert("xss")</script>')
    expect(html).not.toContain('<script>')
  })

  it('空字符串应正常返回', () => {
    const html = render('')
    expect(html).toBeDefined()
  })
})
