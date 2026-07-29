import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
})

// 正则匹配引用标注: [1], [1,2], [1-3], [1,2-4] 等
// eslint-disable-next-line no-useless-escape
const CITATION_RE = /\[(\d+(?:[,\-]\d+)*)\]/g

const SENTINEL = '\0'

/**
 * 在 HTML 文本中将引用标注 [N] 转为可点击的上标元素。
 * 避开已在 <a>, <code>, <pre> 标签内的匹配。
 */
function injectCitationRefs(html: string): string {
  // 用占位符保护代码块和行内代码
  const protectedBlocks: string[] = []
  let protectedHtml = html
    .replace(/<pre[^>]*>[\s\S]*?<\/pre>/gi, (m) => {
      protectedBlocks.push(m)
      return `${SENTINEL}CODEBLOCK${protectedBlocks.length - 1}${SENTINEL}`
    })
    .replace(/<code[^>]*>[\s\S]*?<\/code>/gi, (m) => {
      protectedBlocks.push(m)
      return `${SENTINEL}CODE${protectedBlocks.length - 1}${SENTINEL}`
    })

  protectedHtml = protectedHtml.replace(CITATION_RE, (_match, nums: string) => {
    const refs = nums.split(',').flatMap((part) => {
      if (part.includes('-')) {
        const [start, end] = part.split('-').map(Number)
        if (isNaN(start) || isNaN(end) || start > end) return [part]
        return Array.from({ length: end - start + 1 }, (_, i) => String(start + i))
      }
      return [part]
    })
    const refList = refs.join(',')
    return `<sup class="citation-ref" data-refs="${refList}" title="引用来源 ${nums}">[${nums}]</sup>`
  })

  // 恢复保护块
  const restoreRe = new RegExp(`${SENTINEL}(CODE|CODEBLOCK)(\\d+)${SENTINEL}`, 'g')
  return protectedHtml.replace(restoreRe, (_m, _type, idx) => {
    return protectedBlocks[parseInt(idx)] || ''
  })
}

export function useMarkdown() {
  function render(content: string): string {
    const raw = md.render(content)
    const withCitations = injectCitationRefs(raw)
    return DOMPurify.sanitize(withCitations)
  }

  return { render }
}
