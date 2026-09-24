<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

const storageKey = 'semibrain.sidebar-width'
const viewport = ref(window.innerWidth)
const preferredWidth = ref<number | null>(null)
const dragging = ref(false)
const minimum = 200
const maximum = computed(() => Math.max(minimum, Math.min(520, viewport.value - 480)))
const clamp = (value: number) => Math.round(Math.max(minimum, Math.min(maximum.value, value)))
const width = computed(() => clamp(preferredWidth.value ?? (viewport.value <= 900 ? 205 : 250)))
let drag: { id: number; x: number; width: number; handle: HTMLElement } | null = null

function save() {
  try { localStorage.setItem(storageKey, String(width.value)) } catch { /* Storage may be unavailable. */ }
}
function finish() {
  const previous = drag
  drag = null; dragging.value = false
  document.documentElement.classList.remove('semibrain-sidebar-resizing')
  if (previous?.handle.hasPointerCapture(previous.id)) previous.handle.releasePointerCapture(previous.id)
  if (previous) save()
}
function start(event: PointerEvent) {
  if (event.button !== 0 || viewport.value <= 650 || drag) return
  event.preventDefault()
  const handle = event.currentTarget as HTMLElement
  handle.focus(); handle.setPointerCapture(event.pointerId)
  drag = { id: event.pointerId, x: event.clientX, width: width.value, handle }
  dragging.value = true
  document.documentElement.classList.add('semibrain-sidebar-resizing')
}
function move(event: PointerEvent) {
  if (drag?.id === event.pointerId) preferredWidth.value = clamp(drag.width + event.clientX - drag.x)
}
function end(event: PointerEvent) { if (drag?.id === event.pointerId) finish() }
function keyboard(event: KeyboardEvent) {
  let next: number
  if (event.key === 'ArrowLeft') next = width.value - 16
  else if (event.key === 'ArrowRight') next = width.value + 16
  else if (event.key === 'Home') next = minimum
  else if (event.key === 'End') next = maximum.value
  else return
  event.preventDefault(); preferredWidth.value = clamp(next); save()
}
function resized() {
  finish(); viewport.value = window.innerWidth
}
onMounted(() => {
  try {
    const saved = Number(localStorage.getItem(storageKey))
    if (Number.isFinite(saved) && saved >= minimum && saved <= 520) preferredWidth.value = saved
  } catch { /* Keep the responsive default when storage is unavailable. */ }
  window.addEventListener('resize', resized)
  window.addEventListener('blur', finish)
})
onUnmounted(() => {
  finish()
  window.removeEventListener('resize', resized)
  window.removeEventListener('blur', finish)
})
</script>

<template>
  <aside id="workspace-sidebar" class="sidebar" :style="{ '--sidebar-width': `${width}px` }">
    <slot />
    <div class="sidebar-resizer" :class="{ 'is-dragging': dragging }" role="separator" tabindex="0"
      aria-label="调整侧栏宽度" aria-orientation="vertical" aria-controls="workspace-sidebar"
      :aria-valuemin="minimum" :aria-valuemax="maximum" :aria-valuenow="width" :aria-valuetext="`${width} 像素`"
      title="按住左右拖动，调整侧栏宽度" @pointerdown="start" @pointermove="move"
      @pointerup="end" @pointercancel="end" @lostpointercapture="end" @keydown="keyboard" />
  </aside>
</template>
