<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElDrawer, ElConfigProvider } from 'element-plus'
import 'element-plus/es/components/drawer/style/css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { confirmDiscard, registerEditor } from './editor-state'
const props = withDefaults(defineProps<{ modelValue: boolean; title: string; snapshot?: unknown; busy?: boolean; width?: string; error?: string }>(), { width: '780px' })
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const baseline = ref('')
const serialize = () => JSON.stringify(props.snapshot ?? null)
const dirty = computed(() => props.modelValue && serialize() !== baseline.value)
function markClean() { baseline.value = serialize() }
watch(() => props.modelValue, open => { if (open) markClean() }, { immediate: true, flush: 'post' })
async function close(done?: () => void) {
  if (props.busy || (dirty.value && !await confirmDiscard())) return
  done?.(); emit('update:modelValue', false)
}
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || (props.modelValue && props.busy)) { event.preventDefault(); event.returnValue = '' } }
const unregister = registerEditor(() => !props.modelValue ? false : props.busy ? 'busy' : dirty.value ? 'dirty' : false)
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onUnmounted(() => { unregister(); window.removeEventListener('beforeunload', beforeUnload) })
defineExpose({ close, markClean })
</script>
<template>
  <ElConfigProvider :locale="zhCn"><ElDrawer :model-value="modelValue" :title="title" :size="`min(${width}, 100vw)`" append-to-body destroy-on-close :before-close="close" class="sb-drawer" @update:model-value="emit('update:modelValue', $event)">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <slot />
    <template #footer><span v-if="dirty" class="muted small">有未保存的修改</span><div class="row-actions"><button type="button" class="secondary" :disabled="busy" @click="close()">{{ dirty ? '取消' : '关闭' }}</button><slot name="footer" /></div></template>
  </ElDrawer></ElConfigProvider>
</template>
