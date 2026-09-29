import { api } from './api'

export async function documentCatalog(): Promise<{items: any[]}> {
  const items = new Map<string, any>(), seen = new Set<string>()
  let cursor: string | null = null
  do {
    const page: any = await api('/v1/knowledge/documents' + (cursor ? '?before=' + encodeURIComponent(cursor) : ''))
    for (const item of page.items) items.set(item.id, item)
    cursor = page.next_cursor || null
    if (cursor && seen.has(cursor)) throw new Error('资料目录分页异常，请刷新后重试。')
    if (cursor) seen.add(cursor)
  } while (cursor)
  return {items: [...items.values()]}
}
