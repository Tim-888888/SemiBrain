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
const assetPath = /^\/v1\/assets\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/content(?:\?preview_version=[0-9a-f-]{36})?$/i
md.renderer.rules.image = (tokens, idx, options, env) => {
  const token = tokens[idx], alt = md.utils.escapeHtml(token.content || '原文配图')
  const source = token.attrGet('src')
  const record = env?.images?.find(image => image.url === source)
  const url = record?.display_url || record?.url
  if (!record || !assetPath.test(url)) return `<span class="image-label">[图片：${alt}]</span>`
  return `<span class="inline-image"><img src="${md.utils.escapeHtml(url)}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer" tabindex="0" /><span class="image-fallback" hidden>图片暂不可用：${alt}</span></span>`
}

export function renderMarkdown(source, images = []) {
  return md.render(source || '', { images })
}
