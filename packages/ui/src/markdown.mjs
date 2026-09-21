import MarkdownIt from 'markdown-it'
import katex from 'katex'
import texmath from 'markdown-it-texmath'

const md = new MarkdownIt({ html: false, linkify: false, breaks: false })
md.use(texmath, {
  delimiters: ['brackets', 'dollars'],
  engine: {
    renderToString(source, options) {
      if (source.length > 8000) return `<code>${md.utils.escapeHtml(source)}</code>`
      return katex.renderToString(source, {
        ...options, trust: false, throwOnError: false, maxExpand: 400, maxSize: 20,
        // A fresh macro scope prevents one answer from changing subsequent answers.
        macros: {}, output: 'htmlAndMathml',
      })
    },
  },
})
md.renderer.rules.image = (tokens, idx) => `<span class="image-label">[图片：${md.utils.escapeHtml(tokens[idx].content)}]</span>`

export function renderMarkdown(source) {
  return md.render(source || '')
}
