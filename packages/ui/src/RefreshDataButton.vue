<script setup lang="ts">
import { ref } from 'vue'
import { invalidateReadCache } from './api'
const props = defineProps<{ refresh: () => Promise<unknown>; disabled?: boolean }>()
const busy = ref(false)
async function reload() {
  if (busy.value || props.disabled) return
  busy.value = true
  try { invalidateReadCache(); await props.refresh() } finally { busy.value = false }
}
</script>
<template><button type="button" class="secondary" :disabled="disabled || busy" @click="reload" title="重新获取最新数据">{{ busy ? '刷新中…' : '刷新' }}</button></template>
