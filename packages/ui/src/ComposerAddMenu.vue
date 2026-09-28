<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
const props = defineProps<{ disabled: boolean; actions: { id: string; label: string; description: string; disabled?: boolean }[] }>()
const emit = defineEmits<{ select: [id: string] }>()
const open = ref(false), root = ref<HTMLElement | null>(null), trigger = ref<HTMLButtonElement | null>(null)
function close(focus = false) { open.value = false; if (focus) trigger.value?.focus() }
async function toggle() {
  open.value = !open.value
  if (open.value) { await nextTick(); root.value?.querySelector<HTMLButtonElement>('.composer-add-action:not(:disabled)')?.focus() }
}
function outside(event: PointerEvent) { if (!root.value?.contains(event.target as Node)) close() }
function focusLeft(event: FocusEvent) { if (event.relatedTarget && !root.value?.contains(event.relatedTarget as Node)) close() }
function choose(id: string) { close(); emit('select', id) }
watch(() => props.disabled, value => { if (value) close() })
onMounted(() => document.addEventListener('pointerdown', outside))
onUnmounted(() => document.removeEventListener('pointerdown', outside))
</script>
<template>
  <div ref="root" class="composer-add" @keydown.esc.stop.prevent="close(true)" @focusout="focusLeft">
    <button ref="trigger" type="button" class="composer-add-trigger" :disabled="disabled" aria-label="添加内容" :aria-expanded="open" aria-controls="composer-add-panel" @click="toggle">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
    </button>
    <div v-if="open" id="composer-add-panel" class="composer-add-panel" role="group" aria-label="添加内容选项">
      <p class="composer-add-heading">添加</p>
      <button v-for="action in actions" :key="action.id" type="button" class="composer-add-action" :disabled="action.disabled" @click="choose(action.id)">
        <svg v-if="action.id === 'image'" width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 4-6 5 7"/></svg>
        <span><strong>{{ action.label }}</strong><small>{{ action.description }}</small></span>
      </button>
      <slot />
    </div>
  </div>
</template>
