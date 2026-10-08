// Session-scoped response cache. Nothing is persisted to browser storage.
type Entry = { pending?: Promise<unknown>; value?: unknown; expires: number; size: number }
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value))

export class ReadCache {
  private scope = ''
  private session = 0
  private epoch = 0
  private entries = new Map<string, Entry>()
  constructor(private maxEntries = 64, private maxSize = 4_000_000) {}

  setScope(scope: string) {
    if (scope === this.scope) return
    this.scope = scope; this.session++; this.clear()
  }
  clear() { this.epoch++; this.entries.clear() }

  async read<T>(key: string, loader: () => Promise<T>, ttl: number, force = false, reusable: (value: T) => boolean = () => true): Promise<T> {
    const session = this.session
    // Explicit refresh also fences older reads whose large responses are not retained.
    if (force) { this.clear(); force = false }
    if (!this.scope) return loader()
    for (let attempt = 0; attempt < 3; attempt++) {
      const epoch = this.epoch
      let entry = this.entries.get(key)
      if (!force && entry && entry.expires > Date.now()) {
        const value = copy(entry.value as T)
        await Promise.resolve()
        if (session !== this.session) throw new Error('登录状态已变化，请重新打开页面。')
        if (epoch !== this.epoch) continue
        return value
      }
      if (!entry?.pending) {
        entry = { expires: 0, size: 0 }
        this.entries.set(key, entry)
        entry.pending = Promise.resolve().then(() => {
          if (session !== this.session) throw new Error('登录状态已变化，请重新打开页面。')
          return loader()
        })
      }
      const request = entry
      let value: T
      try { value = await request.pending as T }
      catch (error) { if (this.entries.get(key) === request) this.entries.delete(key); throw error }
      if (session !== this.session) throw new Error('登录状态已变化，请重新打开页面。')
      // A mutation or a newer forced read won the race. Never resurrect its old snapshot.
      if (epoch !== this.epoch || (this.entries.has(key) && this.entries.get(key) !== request)) { force = false; continue }
      const size = JSON.stringify(value).length * 2
      request.pending = undefined
      if (reusable(value) && size <= this.maxSize) {
        request.value = copy(value); request.expires = Date.now() + ttl; request.size = size
        this.entries.delete(key); this.entries.set(key, request)
        let total = [...this.entries.values()].reduce((n, item) => n + item.size, 0)
        for (const [oldKey, old] of this.entries) {
          if (this.entries.size <= this.maxEntries && total <= this.maxSize) break
          if (!old.pending) { this.entries.delete(oldKey); total -= old.size }
        }
      } else if (this.entries.get(key) === request) this.entries.delete(key)
      return copy(value)
    }
    throw new Error('资料正在更新，请稍后刷新重试。')
  }
}

export function catalogIsStable(value: any): boolean {
  return !(value?.items || []).some((item: any) => ['queued', 'running', 'receiving', 'retrying'].includes(item.ingestion?.status) || item.title_status === 'pending')
}

export function readCacheTtl(path: string, method: string): number {
  const [route] = path.split('?')
  if (method === 'POST') return route === '/v1/graph/browse' ? 30_000 : 0
  if (method !== 'GET') return 0
  if (route === '/v1/conversations') return 5_000
  return ['/v1/wiki/pages', '/v1/graph/catalog', '/v1/memories', '/v1/capabilities', '/v1/mcp/services',
    '/admin/v1/agent-configuration', '/admin/v1/skills', '/admin/v1/mcp'].includes(route) ? 30_000 : 0
}

export const readCache = new ReadCache()
