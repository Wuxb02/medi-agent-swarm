import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
})

export function useMarkdown() {
  function render(content: string): string {
    const raw = md.render(content)
    return DOMPurify.sanitize(raw)
  }

  return { render }
}
