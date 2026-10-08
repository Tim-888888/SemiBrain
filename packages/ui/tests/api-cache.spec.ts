import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { api, post, setCacheUser, setCsrf, invalidateReadCache } from '../src/api'
import { documentCatalog } from '../src/document-catalog'
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
const user = { id: 'alice', username: 'alice', role: 'admin' as const, enabled: true, demo: false, revision: 1 }
beforeEach(() => { invalidateReadCache(); setCacheUser(user); setCsrf('csrf-test') })
afterEach(() => { vi.unstubAllGlobals(); setCsrf('') })
it('reuses complete document catalogs across callers rather than mixing pagination snapshots', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response({ items: [{ id: '1' }], next_cursor: 'page2' })).mockResolvedValueOnce(response({ items: [{ id: '2' }], next_cursor: null }))
  vi.stubGlobal('fetch', fetcher)
  const [a, b] = await Promise.all([documentCatalog(), documentCatalog()]); a.items[0].id = 'local'
  expect(b.items.map(item => item.id)).toEqual(['1', '2']); expect((await documentCatalog()).items).toHaveLength(2); expect(fetcher).toHaveBeenCalledTimes(2)
})
it('invalidates related lists after saving; revision checks still go to the server', async () => {
  const fetcher = vi.fn().mockImplementation(async (_path, options) => response(options.method === 'POST' ? { saved: true } : { revision: fetcher.mock.calls.length }))
  vi.stubGlobal('fetch', fetcher)
  expect((await api('/admin/v1/agent-configuration')).revision).toBe(1)
  await api('/admin/v1/agent-configuration'); expect(fetcher).toHaveBeenCalledTimes(1)
  await post('/admin/v1/agent-configuration/drafts', { expected_revision: 1 })
  expect((await api('/admin/v1/agent-configuration')).revision).toBe(3)
  expect(fetcher.mock.calls[1][1].headers.get('X-CSRF-Token')).toBe('csrf-test')
})
it('does not cache write failures or keep previous snapshots after a denied operation', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response({ revision: 1 })).mockResolvedValueOnce(response({ error: { code: 'REVISION_CONFLICT' } }, 409)).mockResolvedValue(response({ revision: 2 }))
  vi.stubGlobal('fetch', fetcher); await api('/admin/v1/mcp')
  await expect(post('/admin/v1/mcp', {})).rejects.toThrow('已更新')
  expect((await api('/admin/v1/mcp')).revision).toBe(2)
})
it('keys query variants and read-only graph payloads independently', async () => {
  const fetcher = vi.fn(async () => response({ value: 1 })); vi.stubGlobal('fetch', fetcher)
  await api('/v1/mcp/services?multi_agent=false'); await api('/v1/mcp/services?multi_agent=true'); await api('/v1/mcp/services?multi_agent=false')
  await post('/v1/graph/browse', { limit: 100 }); await post('/v1/graph/browse', { limit: 500 }); await post('/v1/graph/browse', { limit: 100 })
  expect(fetcher).toHaveBeenCalledTimes(4)
})
it('manual reload bypasses a warm cache and live diagnostics always fetch', async () => {
  const fetcher = vi.fn(async () => response({ items: [] })); vi.stubGlobal('fetch', fetcher)
  await api('/v1/memories'); await api('/v1/memories', { cache: 'reload' }); await api('/v1/memories')
  await api('/admin/v1/runs/x/diagnostics'); await api('/admin/v1/runs/x/diagnostics')
  expect(fetcher).toHaveBeenCalledTimes(4)
})
it('signals are not shared between callers and clearing session drops all cached data', async () => {
  const fetcher = vi.fn(async () => response({ items: [] })); vi.stubGlobal('fetch', fetcher)
  await api('/v1/memories', { signal: new AbortController().signal }); await api('/v1/memories')
  setCsrf(''); setCacheUser({ ...user, id: 'bob' }); await api('/v1/memories')
  expect(fetcher).toHaveBeenCalledTimes(3)
})
