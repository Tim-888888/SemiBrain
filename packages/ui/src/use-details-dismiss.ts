import { onMounted, onUnmounted, type Ref } from 'vue'

export function useDetailsDismiss(root: Ref<HTMLDetailsElement | null>) {
  function outside(event: Event) {
    const menu = root.value
    if (menu?.open && !event.composedPath().includes(menu)) menu.open = false
  }

  function escape(event: KeyboardEvent) {
    const menu = root.value
    if (event.key !== 'Escape' || !menu?.open) return
    menu.open = false
    event.preventDefault()
    event.stopPropagation()
    menu.querySelector('summary')?.focus()
  }

  onMounted(() => {
    document.addEventListener('pointerdown', outside, true)
    document.addEventListener('focusin', outside, true)
    document.addEventListener('keydown', escape)
  })
  onUnmounted(() => {
    document.removeEventListener('pointerdown', outside, true)
    document.removeEventListener('focusin', outside, true)
    document.removeEventListener('keydown', escape)
  })
}
