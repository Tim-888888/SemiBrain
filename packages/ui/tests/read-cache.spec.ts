import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ReadCache, catalogIsStable, readCacheTtl } from '../src/read-cache'
const deferred = <T>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
describe('session read cache', () => {
  let cache: ReadCache
  beforeEach(() => { vi.useFakeTimers(); cache = new ReadCache(); cache.setScope('alice:admin:1') })
  afterEach(() => vi.useRealTimers())
  it('deduplicates concurrent reads, returns isolated snapshots and expires without extending on hits', async () => {
    const load = vi.fn(async () => ({ items: [{ title: 'original' }] }))
    const [a, b] = await Promise.all([cache.read('list', load, 30_000), cache.read('list', load, 30_000)])
    a.items[0].title = 'edited locally'
    expect(b.items[0].title).toBe('original'); expect(load).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(29_999); expect((await cache.read('list', load, 30_000)).items[0].title).toBe('original')
    vi.advanceTimersByTime(1); await cache.read('list', load, 30_000); expect(load).toHaveBeenCalledTimes(2)
  })
  it('forces a new read and does not serve stale data when it fails', async () => {
    const load = vi.fn().mockResolvedValueOnce('old').mockRejectedValueOnce(new Error('offline')).mockResolvedValue('new')
    await cache.read('x', load, 1000)
    await expect(cache.read('x', load, 1000, true)).rejects.toThrow('offline')
    expect(await cache.read('x', load, 1000)).toBe('new')
  })
  it('never caches failures', async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error('denied')).mockResolvedValue(2)
    await expect(cache.read('x', load, 1000)).rejects.toThrow('denied')
    expect(await cache.read('x', load, 1000)).toBe(2)
  })
  it('rejects late responses after account, role or session changes', async () => {
    const old = deferred<string>(); const pending = cache.read('x', () => old.promise, 1000)
    cache.setScope('bob:user:1'); old.resolve('alice private data')
    await expect(pending).rejects.toThrow('登录状态已变化')
    expect(await cache.read('x', async () => 'bob data', 1000)).toBe('bob data')
    cache.setScope('bob:user:2'); expect(await cache.read('x', async () => 'revised scope', 1000)).toBe('revised scope')
    cache.setScope(''); const load = vi.fn(async () => 'anonymous'); await cache.read('x', load, 1000); await cache.read('x', load, 1000)
    expect(load).toHaveBeenCalledTimes(2)
  })
  it('also rejects a cache hit racing logout', async () => {
    await cache.read('x', async () => 'private', 1000)
    const hit = cache.read('x', async () => 'unused', 1000); cache.setScope('')
    await expect(hit).rejects.toThrow('登录状态已变化')
  })
  it('refetches an in-flight read invalidated by a mutation', async () => {
    const old = deferred<string>(); const load = vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue('after-write')
    const result = cache.read('x', load, 1000); await Promise.resolve(); cache.clear(); old.resolve('before-write')
    expect(await result).toBe('after-write'); expect(await cache.read('x', load, 1000)).toBe('after-write'); expect(load).toHaveBeenCalledTimes(2)
  })
  it('a slower old response cannot overwrite a newer forced response', async () => {
    const old = deferred<string>(); const slow = cache.read('x', () => old.promise, 1000)
    expect(await cache.read('x', async () => 'fresh', 1000, true)).toBe('fresh')
    old.resolve('old'); expect(await slow).toBe('fresh')
  })
  it('bounds entries and response size', async () => {
    cache = new ReadCache(2, 60); cache.setScope('a')
    const load = vi.fn(async () => 'a')
    await cache.read('a', load, 1000); await cache.read('b', load, 1000); await cache.read('c', load, 1000)
    await cache.read('a', load, 1000); expect(load).toHaveBeenCalledTimes(4)
    const big = vi.fn(async () => 'x'.repeat(60)); await cache.read('big', big, 1000); await cache.read('big', big, 1000)
    expect(big).toHaveBeenCalledTimes(2)
  })
  it.each(['queued', 'running', 'receiving', 'retrying'])('does not freeze ingestion status %s', async status => {
    const load = vi.fn(async () => ({ items: [{ ingestion: { status } }] }))
    await cache.read('x', load, 1000, false, catalogIsStable); await cache.read('x', load, 1000, false, catalogIsStable)
    expect(load).toHaveBeenCalledTimes(2)
  })
  it('does not freeze pending generated titles', () => expect(catalogIsStable({ items: [{ title_status: 'pending' }] })).toBe(false))
  it('only opts in known reads; live state, credentials, previews and downloads bypass cache', () => {
    for (const route of ['/v1/auth/me', '/v1/auth/captcha', '/admin/v1/users', '/admin/v1/evaluations', '/admin/v1/operations/queues', '/admin/v1/runs/comparison', '/v1/runs/123', '/admin/v1/runs/123/diagnostics', '/v1/conversations/123/messages', '/v1/conversations/123/title', '/v1/assets/123/content', '/v1/runs/123/exports', '/v1/wiki/pages/123']) expect(readCacheTtl(route, 'GET')).toBe(0)
    expect(readCacheTtl('/v1/mcp/services?multi_agent=true', 'GET')).toBe(30_000)
    expect(readCacheTtl('/admin/v1/mcp', 'POST')).toBe(0)
    expect(readCacheTtl('/v1/graph/browse', 'POST')).toBe(30_000)
  })
})
