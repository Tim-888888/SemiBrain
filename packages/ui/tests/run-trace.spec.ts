import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import RunTrace from '../src/RunTrace.vue'
import { api } from '../src/api'
import { readCacheTtl } from '../src/read-cache'

vi.mock('../src/api', () => ({ api: vi.fn() }))
afterEach(() => vi.resetAllMocks())
const result = () => ({
  status: 'succeeded', counts: { model: 2, tool: 1 }, truncated: false,
  items: [
    { id: '1', kind: 'model', phase: 'quick_answer', status: 'completed', usage: { total_tokens: 10 }, profile: { model: 'deepseek-flash' }, elapsed_ms: 1200 },
    { id: '2', kind: 'model', phase: 'review', status: 'completed', usage: {}, profile: { model: 'deepseek-flash' } },
    { id: '3', kind: 'tool', tool: 'knowledge.search', status: 'succeeded', usage: {}, elapsed_ms: 123 },
  ],
  trace: { url: 'https://langfuse.example.com/project/p/traces/t' },
  telemetry: { status: 'available', truncated: false, items: [
    { id: 'span-1', parent_id: 'not-returned', name: 'quick_answer', kind: 'model', status: 'completed', elapsed_ms: 1200, usage: { total_tokens: 10 } },
    { id: 'span-2', parent_id: 'span-1', name: 'child', kind: 'span', status: 'completed', usage: {} },
  ] },
})

describe('owned run trace', () => {
  it('uses the bound admin SSO entry and keeps it hidden from ordinary users', async () => {
    const value = { ...result(), trace: { ...result().trace, sso_url: '/v1/auth/langfuse/open?run_id=run' } }
    vi.mocked(api).mockResolvedValue(value)
    const w = mount(RunTrace, { props: { runId: 'run', isAdmin: true } }); await flushPromises()
    expect(w.get('a').attributes('href')).toBe(value.trace.sso_url)
    expect(w.text()).toContain('管理员统一登录')
    await w.setProps({ isAdmin: false })
    expect(w.find('a').exists()).toBe(false); w.unmount()
  })
  it('uses the owner endpoint and hides external links from ordinary accounts', async () => {
    vi.mocked(api).mockResolvedValue(result())
    const w = mount(RunTrace, { props: { runId: 'run' } }); await flushPromises()
    expect(api).toHaveBeenCalledWith('/v1/runs/run/trace', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(w.find('a').exists()).toBe(false)
    expect(w.findAll('.trace-metrics strong')[2].text()).toBe('10') // Do not double-count telemetry.
    expect(w.text()).toContain('1 次请求的用量待核验')
    await w.find('select').setValue('tool')
    expect(w.findAll('.trace-step')).toHaveLength(1)
    expect(w.text()).toContain('knowledge.search'); w.unmount()
  })

  it('retains the full backend for admins and renders orphan and child relationships', async () => {
    vi.mocked(api).mockResolvedValue(result())
    const w = mount(RunTrace, { props: { runId: 'run', isAdmin: true } }); await flushPromises()
    expect(w.get('a').attributes('href')).toBe(result().trace.url)
    expect(w.get('a').attributes('rel')).toBe('noopener noreferrer')
    await w.findAll('.trace-tabs button')[1].trigger('click')
    expect(w.text()).toContain('上游节点未返回')
    expect(w.findAll('.trace-span')).toHaveLength(2)
    expect(w.findAll('.trace-span')[1].text()).toContain('生成回答'); w.unmount()
  })

  it('retains durable calls when telemetry is unavailable and refreshes on demand', async () => {
    const value = result(); value.telemetry = { status: 'unavailable', items: [], truncated: false }
    vi.mocked(api).mockResolvedValue(value)
    const w = mount(RunTrace, { props: { runId: 'run' } }); await flushPromises()
    expect(w.text()).toContain('遥测服务暂时不可用')
    expect(w.findAll('.trace-step')).toHaveLength(3)
    await w.get('.trace-heading button').trigger('click'); await flushPromises()
    expect(api).toHaveBeenCalledTimes(2); w.unmount()
  })

  it('does not resurrect a previous run when requests finish out of order', async () => {
    let old: (value: unknown) => void = () => {}
    vi.mocked(api).mockImplementationOnce(() => new Promise(resolve => { old = resolve }))
    const w = mount(RunTrace, { props: { runId: 'old' } })
    vi.mocked(api).mockRejectedValueOnce(new Error('运行不存在或当前账号无权查看。'))
    await w.setProps({ runId: 'new' }); await flushPromises()
    old(result()); await flushPromises()
    expect(w.get('[role=alert]').text()).toContain('无权查看')
    expect(w.find('.trace-metrics').exists()).toBe(false)
    expect(w.text()).not.toContain('deepseek-flash'); w.unmount()
  })

  it('never caches trace responses and aborts requests on drawer close', () => {
    vi.mocked(api).mockReturnValue(new Promise(() => {}))
    const w = mount(RunTrace, { props: { runId: 'run' } })
    const signal = vi.mocked(api).mock.calls[0][1]?.signal
    w.unmount(); expect(signal?.aborted).toBe(true)
    expect(readCacheTtl('/v1/runs/run/trace', 'GET')).toBe(0)
  })
})
