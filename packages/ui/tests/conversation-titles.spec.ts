import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent } from 'vue'
import Editor from '../src/ConversationTitleEditor.vue'
import { mergeTitle } from '../src/conversation-titles'
import { api, post } from '../src/api'
vi.mock('../src/api', () => ({ api: vi.fn(), post: vi.fn() }))
vi.mock('../src/ResourceDrawer.vue', () => ({ default: defineComponent({ template: '<slot/>' }) }))
const Drawer = defineComponent({
  props: ['modelValue', 'error'], emits: ['update:modelValue'],
  template: '<section><p role="alert">{{error}}</p><slot/><slot name="footer"/></section>',
})
let wrapper: VueWrapper | undefined
const original = { id: 'c', title: '原来的名字', title_revision: 3, title_source: 'manual', title_status: 'idle' }
async function open(mode: 'rename' | 'generate' = 'rename') {
  wrapper = mount(Editor, { props: { item: original, mode }, global: { stubs: { ResourceDrawer: Drawer } } })
  await flushPromises()
  return wrapper
}
beforeEach(() => {
  vi.mocked(api).mockResolvedValue(original)
  vi.mocked(post).mockResolvedValue({ ...original, title: '我的工程笔记', title_revision: 4 })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.clearAllMocks(); vi.useRealTimers() })
it('saves only title metadata and emits a shared sidebar/topbar update', async () => {
  const w = await open()
  await w.find('input').setValue('我的工程笔记')
  await w.find('form').trigger('submit'); await flushPromises()
  const [path, body] = vi.mocked(post).mock.calls[0]
  expect(path).toBe('/v1/conversations/c/title')
  expect(body).toMatchObject({ title: '我的工程笔记', expected_title_revision: 3 })
  expect(body).not.toHaveProperty('expected_revision')
  expect(w.emitted('saved')?.[0]?.[0]).toMatchObject({ title: '我的工程笔记', title_revision: 4 })
})
it('previews generated text without saving automatically', async () => {
  vi.mocked(api).mockImplementation(async path => path.endsWith('/title')
    ? original : { status: 'ready', title: 'CP 与 FT 的测试阶段', base_revision: 3 })
  vi.mocked(post).mockResolvedValue({ id: 'job' })
  const w = await open('generate')
  expect((w.find('input').element as HTMLInputElement).value).toBe('CP 与 FT 的测试阶段')
  expect(w.text()).toContain('保存后才会替换原标题')
  expect(post).toHaveBeenCalledTimes(1)
  expect(w.emitted('saved')).toBeUndefined()
})
it('preserves the manual title and allows editing after model failure', async () => {
  vi.mocked(api).mockImplementation(async path => path.endsWith('/title') ? original : { status: 'failed' })
  vi.mocked(post).mockResolvedValue({ id: 'job' })
  const w = await open('generate')
  expect((w.find('input').element as HTMLInputElement).value).toBe(original.title)
  expect(w.find('input').attributes('disabled')).toBeUndefined()
  expect(w.text()).toContain('原标题已保留')
})
it('retains unsaved text on revision conflict', async () => {
  vi.mocked(post).mockRejectedValue(new Error('标题已在其他页面更新'))
  const w = await open()
  await w.find('input').setValue('待保存名字')
  await w.find('form').trigger('submit'); await flushPromises()
  expect((w.find('input').element as HTMLInputElement).value).toBe('待保存名字')
  expect(w.text()).toContain('标题已在其他页面更新')
  expect(w.emitted('close')).toBeUndefined()
})
it('stops polling when editor closes without changing another conversation', async () => {
  vi.useFakeTimers()
  vi.mocked(api).mockImplementation(async path => path.endsWith('/title') ? original : { status: 'queued' })
  vi.mocked(post).mockResolvedValue({ id: 'job' })
  const w = await open('generate')
  const calls = vi.mocked(api).mock.calls.length
  w.unmount(); wrapper = undefined
  await vi.advanceTimersByTimeAsync(10000)
  expect(api).toHaveBeenCalledTimes(calls)
  expect(w.emitted('saved')).toBeUndefined()
})
it('rejects stale title responses while preserving message revision and active run', () => {
  const running = { ...original, title_revision: 5, revision: 9, activeRun: { status: 'running' } }
  expect(mergeTitle(running, { ...original, title_revision: 4, title: '过期自动标题' })).toEqual(running)
  const updated = mergeTitle(running, { ...original, title_revision: 6, title: '新标题' })
  expect(updated.title).toBe('新标题')
  expect(updated.revision).toBe(9)
  expect(updated.activeRun).toBe(running.activeRun)
})
