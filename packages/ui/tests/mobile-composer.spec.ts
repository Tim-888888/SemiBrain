import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { useMobileComposer } from '../src/use-mobile-composer'

let viewport: EventTarget & { height: number; offsetTop: number; scale: number }
let frames: FrameRequestCallback[], wrapper: ReturnType<typeof mount> | undefined
let enabled: ReturnType<typeof ref<boolean>>, text: ReturnType<typeof ref<string>>
let resizeCallback: () => void, disconnect: ReturnType<typeof vi.fn>
const settle = async () => { await nextTick(); const pending = frames.splice(0); pending.forEach(f => f(0)); await flushPromises() }
function setup(mobile = true) {
  enabled = ref(mobile); text = ref('')
  wrapper = mount(defineComponent({
    setup() {
      const input = ref<HTMLTextAreaElement | null>(null), area = ref<HTMLElement | null>(null)
      return { input, area, text, ...useMobileComposer(enabled, input, area, text) }
    },
    template: '<div :style="viewportStyle" :class="{keyboard:keyboardOpen}"><div ref="area"><textarea ref="input" v-model="text" rows="2" style="line-height:24px;padding:12px 2px" /></div></div>',
  }), { attachTo: document.body })
  const field = wrapper.find('textarea').element
  Object.defineProperty(field, 'scrollHeight', { configurable: true, get: () => text.value ? 240 : 48 })
  return field
}
beforeEach(() => {
  frames = []; disconnect = vi.fn()
  viewport = Object.assign(new EventTarget(), {height:844,offsetTop:0,scale:1})
  vi.stubGlobal('visualViewport', viewport)
  vi.stubGlobal('innerHeight', 844); vi.stubGlobal('innerWidth', 390)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {frames.push(callback); return frames.length})
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) {resizeCallback=callback} observe() {} disconnect=disconnect })
})
afterEach(() => { wrapper?.unmount(); wrapper=undefined; vi.unstubAllGlobals(); document.body.innerHTML='' })

describe('mobile-only composer presentation', () => {
  it('caps long drafts at four lines, then shrinks after clearing without changing draft content', async () => {
    const field=setup(); await settle(); expect(field.style.height).toBe('48px')
    text.value='A long unchanged draft'; await settle()
    expect(field.value).toBe('A long unchanged draft'); expect(field.style.height).toBe('120px'); expect(field.style.overflowY).toBe('auto')
    text.value=''; await settle(); expect(field.style.height).toBe('48px'); expect(field.style.overflowY).toBe('hidden')
  })
  it('tracks keyboard resize/offset and keyboard dismissal while preserving focus and the draft', async () => {
    const field=setup(); await settle(); field.focus(); text.value='Draft'; await settle()
    viewport.height=490; viewport.offsetTop=12; viewport.dispatchEvent(new Event('resize')); await settle()
    expect(wrapper!.classes()).toContain('keyboard'); expect(wrapper!.attributes('style')).toContain('--mobile-viewport-height: 490px')
    expect(wrapper!.attributes('style')).toContain('--mobile-viewport-top: 12px')
    viewport.height=844; viewport.offsetTop=0; viewport.dispatchEvent(new Event('resize')); await settle()
    expect(wrapper!.classes()).not.toContain('keyboard'); expect(field.value).toBe('Draft'); expect(document.activeElement).toBe(field)
  })
  it('does not treat address-bar resizing or pinch zoom as a keyboard', async () => {
    const field=setup(); await settle(); field.focus(); await settle()
    viewport.height=790; viewport.dispatchEvent(new Event('resize')); await settle(); expect(wrapper!.classes()).not.toContain('keyboard')
    viewport.scale=2; viewport.height=400; viewport.dispatchEvent(new Event('resize')); await settle()
    expect(wrapper!.classes()).not.toContain('keyboard'); expect(wrapper!.attributes('style')).toContain('790px')
  })
  it('leaves desktop unchanged and removes mobile overrides when crossing the breakpoint', async () => {
    const field=setup(false); await settle(); expect(field.style.height).toBe(''); expect(wrapper!.attributes('style') || '').toBe('')
    enabled.value=true; await settle(); expect(field.style.height).toBe('48px')
    enabled.value=false; await settle(); expect(field.style.height).toBe(''); expect(field.style.overflowY).toBe(''); expect(wrapper!.attributes('style') || '').toBe('')
  })
  it('repositions menus after attachment changes without altering the draft or scroll position', async () => {
    const field=setup(); await settle(); text.value='Keep this draft'; await settle()
    const area=wrapper!.find('textarea').element.parentElement!
    vi.spyOn(area,'getBoundingClientRect').mockReturnValue({top:650} as DOMRect)
    resizeCallback(); await settle(); expect(wrapper!.attributes('style')).toContain('--mobile-menu-top: 644px')
    vi.spyOn(area,'getBoundingClientRect').mockReturnValue({top:250} as DOMRect)
    resizeCallback(); await settle(); expect(wrapper!.attributes('style')).toContain('--mobile-menu-height: 238px')
    expect(field.value).toBe('Keep this draft')
    wrapper!.unmount(); wrapper=undefined; frames=[]
    viewport.dispatchEvent(new Event('resize')); document.dispatchEvent(new Event('focusin')); expect(frames).toHaveLength(0); expect(disconnect).toHaveBeenCalled()
  })
})
