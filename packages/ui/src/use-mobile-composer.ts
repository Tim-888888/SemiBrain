import { nextTick, onMounted, onUnmounted, ref, watch, type Ref } from 'vue'

/** Presentation only: keep the mobile composer inside the visible viewport. */
export function useMobileComposer(
  enabled: Ref<boolean>, input: Ref<HTMLTextAreaElement | null>,
  area: Ref<HTMLElement | null>, text: Ref<string>,
) {
  const keyboardOpen = ref(false)
  const viewportStyle = ref<Record<string, string>>({})
  let frame = 0, closedHeight = 0, lastWidth = 0
  let observer: ResizeObserver | undefined
  const viewport = window.visualViewport

  function update() {
    frame = 0
    const field = input.value
    if (!enabled.value) {
      keyboardOpen.value = false
      viewportStyle.value = {}
      closedHeight = 0
      field?.style.removeProperty('height')
      field?.style.removeProperty('overflow-y')
      return
    }
    // Pinch zoom is not a keyboard resize. Leave the layout at its original size.
    if (viewport && Math.abs(viewport.scale - 1) > 0.01) return
    const height = viewport?.height || window.innerHeight
    const top = viewport?.offsetTop || 0
    if (lastWidth !== window.innerWidth) closedHeight = 0
    lastWidth = window.innerWidth
    const focused = document.activeElement === field
    if (!focused && !keyboardOpen.value) closedHeight = height
    closedHeight = Math.max(closedHeight, height)
    const fullHeight = Math.max(window.innerHeight, closedHeight)
    keyboardOpen.value = (focused || keyboardOpen.value) && fullHeight - height > Math.max(120, fullHeight * .18)
    viewportStyle.value = { ...viewportStyle.value,
      '--mobile-viewport-height': `${height}px`, '--mobile-viewport-top': `${top}px`,
    }
    // Measure after Vue has applied the viewport and keyboard classes, including
    // position-only changes (address bar / panning) that ResizeObserver misses.
    void nextTick(() => layout(top))
  }
  function layout(top: number) {
    if (!enabled.value) return
    const field = input.value
    if (field) {
      const style = getComputedStyle(field)
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)
      const line = parseFloat(style.lineHeight) || 24
      field.style.height = '0px'
      const max = line * 4 + padding
      const desired = Math.max(line + padding, field.scrollHeight)
      field.style.height = `${Math.min(desired, max)}px`
      field.style.overflowY = desired > max ? 'auto' : 'hidden'
    }
    // Fixed menus escape the horizontally scrolling chip row without teleporting
    // their DOM, preserving the existing outside-click and keyboard dismissal.
    const areaTop = area.value?.getBoundingClientRect().top
    if (areaTop !== undefined) {
      const menuHeight = Math.max(48, Math.min(300, areaTop - top - 12))
      viewportStyle.value['--mobile-menu-top'] = `${Math.max(top + menuHeight + 6, areaTop - 6)}px`
      viewportStyle.value['--mobile-menu-height'] = `${menuHeight}px`
    }
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(update) }
  watch([enabled, input, text], () => { void nextTick(schedule) }, { flush: 'post' })
  watch(area, value => { observer?.disconnect(); if (value) observer?.observe(value); schedule() }, { flush: 'post' })
  onMounted(() => {
    observer = new ResizeObserver(schedule)
    if (area.value) observer.observe(area.value)
    viewport?.addEventListener('resize', schedule)
    viewport?.addEventListener('scroll', schedule)
    window.addEventListener('resize', schedule)
    document.addEventListener('focusin', schedule)
    document.addEventListener('focusout', schedule)
    schedule()
  })
  onUnmounted(() => {
    cancelAnimationFrame(frame)
    observer?.disconnect()
    viewport?.removeEventListener('resize', schedule)
    viewport?.removeEventListener('scroll', schedule)
    window.removeEventListener('resize', schedule)
    document.removeEventListener('focusin', schedule)
    document.removeEventListener('focusout', schedule)
  })
  return { keyboardOpen, viewportStyle }
}
