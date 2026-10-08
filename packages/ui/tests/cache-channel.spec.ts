import { afterEach, beforeEach, expect, it, vi } from 'vitest'
class Channel {
  static latest: Channel
  onmessage: ((event: { data: string }) => void) | null = null
  postMessage = vi.fn()
  constructor() { Channel.latest = this }
}
beforeEach(() => { vi.resetModules(); vi.stubGlobal('BroadcastChannel', Channel) })
afterEach(() => vi.unstubAllGlobals())
const user = { id: 'a', username: 'a', role: 'admin' as const, revision: 1, enabled: true, demo: false }
it('invalidates another tab without sharing response data or credentials', async () => {
  const { api, post, setCacheUser } = await import('../src/api'); setCacheUser(user)
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ revision: 1 }))); vi.stubGlobal('fetch', fetcher)
  await api('/admin/v1/skills'); await api('/admin/v1/skills'); expect(fetcher).toHaveBeenCalledTimes(1)
  Channel.latest.onmessage?.({ data: 'invalidate' }); await api('/admin/v1/skills'); expect(fetcher).toHaveBeenCalledTimes(2)
  await post('/admin/v1/skills', { secret: 'never-broadcast' })
  expect(Channel.latest.postMessage.mock.calls).toEqual([['invalidate']])
})
it('clears an existing workspace session after login/logout in another tab', async () => {
  const { api, setCacheUser } = await import('../src/api'); setCacheUser(user)
  const listener = vi.fn(); window.addEventListener('semibrain:session-expired', listener)
  const fetcher = vi.fn(async () => new Response('{}')); vi.stubGlobal('fetch', fetcher)
  await api('/v1/memories'); Channel.latest.onmessage?.({ data: 'session' })
  expect(listener).toHaveBeenCalledOnce(); await api('/v1/memories'); await api('/v1/memories'); expect(fetcher).toHaveBeenCalledTimes(3)
  window.removeEventListener('semibrain:session-expired', listener)
})
it('a failed login must not log out other tabs', async () => {
  const { post } = await import('../src/api')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { code: 'LOGIN_FAILED' } }), { status: 400 })))
  await expect(post('/v1/auth/login', {})).rejects.toThrow()
  expect(Channel.latest.postMessage.mock.calls).toEqual([['invalidate']])
})
