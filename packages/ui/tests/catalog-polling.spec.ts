import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { useCatalogPolling } from '../src/catalog-polling'
let wrapper: VueWrapper | undefined
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(100_000); Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true }) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers(); Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true }) })
function setup(load: () => Promise<void>, active: () => boolean) { wrapper = mount(defineComponent({ setup() { useCatalogPolling(load, active); return () => null } })) }
it('refreshes idle catalogs every 30 seconds rather than every 5 seconds', async () => {
  const load = vi.fn(async () => {}); setup(load, () => false); await flushPromises()
  await vi.advanceTimersByTimeAsync(29_999); expect(load).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1); expect(load).toHaveBeenCalledTimes(2)
})
it('continues 5-second polling while ingestion is running', async () => {
  let running = true; const load = vi.fn(async () => {}); setup(load, () => running); await flushPromises()
  await vi.advanceTimersByTimeAsync(10_000); expect(load).toHaveBeenCalledTimes(3)
  running = false; await vi.advanceTimersByTimeAsync(25_000); expect(load).toHaveBeenCalledTimes(3)
  await vi.advanceTimersByTimeAsync(5_000); expect(load).toHaveBeenCalledTimes(4)
})
it('stops network polling in a hidden tab and resumes on visibility', async () => {
  const load = vi.fn(async () => {}); setup(load, () => true); await flushPromises()
  Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
  await vi.advanceTimersByTimeAsync(60_000); expect(load).toHaveBeenCalledTimes(1)
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true }); document.dispatchEvent(new Event('visibilitychange')); await flushPromises()
  expect(load).toHaveBeenCalledTimes(2)
  wrapper?.unmount(); wrapper = undefined; await vi.advanceTimersByTimeAsync(60_000); document.dispatchEvent(new Event('visibilitychange'))
  expect(load).toHaveBeenCalledTimes(2)
})
it('does not overlap slow catalog requests', async () => {
  let done!: () => void; const load = vi.fn(() => new Promise<void>(resolve => { done = resolve })); setup(load, () => true)
  await vi.advanceTimersByTimeAsync(20_000); expect(load).toHaveBeenCalledTimes(1)
  done(); await flushPromises(); await vi.advanceTimersByTimeAsync(5000); expect(load).toHaveBeenCalledTimes(2)
})
