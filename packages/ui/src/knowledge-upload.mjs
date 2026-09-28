import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false })
export const documentFile = file => /\.(md|pdf|docx|csv)$/i.test(file.name)
const pathOf = file => file.webkitRelativePath || file.name

export function resolveImagePath(documentPath, reference) {
  let ref
  try { ref = decodeURIComponent(reference).replaceAll('\\', '/') } catch { return null }
  if (/^(?:[a-z][\w+.-]*:|\/)/i.test(ref)) return null
  const parts = documentPath.split('/').slice(0, -1)
  for (const part of ref.split(/[?#]/, 1)[0].split('/')) {
    if (part === '..') { if (!parts.length) return null; parts.pop() }
    else if (part && part !== '.') parts.push(part)
  }
  return parts.join('/')
}

export async function documentBundle(file, files, documentPath) {
  if (!/\.md$/i.test(file.name)) return []
  const available = new Map(files.map(f => [pathOf(f), f]))
  const result = new Map()
  function visit(tokens) {
    for (const token of tokens) {
      if (token.type === 'image') {
        const path = resolveImagePath(pathOf(file), token.attrGet('src') || '')
        const target = path && available.get(path)
        if (!target) throw new Error(`图片未找到：${token.attrGet('src')}。请选择包含 Markdown 和配图的完整文件夹。`)
        const uploadedPath = resolveImagePath(documentPath, token.attrGet('src') || '')
        if (!uploadedPath) throw new Error('图片路径超出所选文档目录。')
        result.set(uploadedPath, { path: uploadedPath, file: target })
      }
      if (token.children) visit(token.children)
    }
  }
  visit(md.parse(await file.text(), {}))
  const images = [...result.values()]
  if (images.length > 50 || file.size + images.reduce((n, r) => n+r.file.size, 0) > 32*1024*1024) {
    throw new Error('单篇文档及其配图合计不能超过 32 MB，配图不能超过 50 张。')
  }
  return images
}
