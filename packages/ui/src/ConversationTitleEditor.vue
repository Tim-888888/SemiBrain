<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import ResourceDrawer from './ResourceDrawer.vue'
import { api, post } from './api'
import type { ConversationTitle } from './conversation-titles'
const props = defineProps<{ item: { id: string; title: string }; mode: 'rename' | 'generate' }>()
const emit = defineEmits<{ close: []; saved: [value: ConversationTitle] }>()
const title = ref(props.item.title), revision = ref(0), error = ref(''), notice = ref('')
const loading = ref(true), saving = ref(false), generating = ref(false), ready = ref(false)
const field = ref<HTMLInputElement | null>(null)
let alive = true, timer: ReturnType<typeof setTimeout> | undefined, polling = 0
onUnmounted(() => { alive = false; clearTimeout(timer) })
onMounted(async () => {
  try {
    const value = await api<ConversationTitle>(`/v1/conversations/${props.item.id}/title`)
    if (!alive) return
    title.value = value.title; revision.value = value.title_revision; ready.value = true
    if (props.mode === 'generate') await generate()
    else field.value?.focus()
  } catch (e) { if (alive) error.value = (e as Error).message }
  finally { if (alive) loading.value = false }
})
async function generate() {
  if (generating.value || !ready.value) return
  generating.value = true; error.value = ''; notice.value = ''; polling = 0
  try {
    const id = crypto.randomUUID()
    const result = await post(`/v1/conversations/${props.item.id}/title-suggestions`,
      { request_id: id, expected_title_revision: revision.value }, id)
    if (alive) await poll(result.id)
  } catch (e) { if (alive) { error.value = (e as Error).message; generating.value = false } }
}
async function poll(id: string) {
  try {
    const result = await api(`/v1/conversations/${props.item.id}/title-suggestions/${id}`)
    if (!alive) return
    if (result.status === 'ready') {
      title.value = result.title; revision.value = result.base_revision
      notice.value = '模型建议已填入，可继续修改。保存后才会替换原标题。'
      generating.value = false; field.value?.focus(); return
    }
    if (['failed', 'superseded'].includes(result.status)) throw new Error(result.status === 'superseded'
      ? '原标题已变化，请重新打开窗口。' : '生成暂未成功，原标题已保留。可以手动命名或稍后重试。')
    if (++polling >= 60) throw new Error('生成等待超时，原标题已保留。可以手动命名或稍后重试。')
    timer = setTimeout(() => { void poll(id) }, 1500)
  } catch (e) { if (alive) { error.value = (e as Error).message; generating.value = false } }
}
async function save() {
  if (!ready.value || saving.value || generating.value || !title.value.trim()) return
  saving.value = true; error.value = ''
  try {
    const id = crypto.randomUUID()
    const result = await post<ConversationTitle>(`/v1/conversations/${props.item.id}/title`,
      { request_id: id, expected_title_revision: revision.value, title: title.value.trim() }, id)
    if (alive) { emit('saved', result); emit('close') }
  } catch (e) { if (alive) error.value = (e as Error).message }
  finally { if (alive) saving.value = false }
}
</script>
<template>
  <ResourceDrawer :model-value="true" title="会话标题" width="480px" :busy="saving" :error="error" @update:model-value="!$event && emit('close')">
    <p class="muted">为会话起一个容易查找的名字。改名不会改变消息和回答。</p>
    <form id="conversation-title-editor" @submit.prevent="save">
      <label for="conversation-title-input">标题</label>
      <input id="conversation-title-input" ref="field" v-model="title" maxlength="120" :disabled="loading || saving || generating" required autocomplete="off" />
      <p class="small muted">{{ title.length }} / 120</p>
    </form>
    <button type="button" class="secondary" :disabled="!ready || saving || generating" @click="generate">{{ generating ? '正在生成建议…' : '重新生成标题' }}</button>
    <p class="small muted" role="status">{{ generating ? '根据会话主题生成，原标题暂不改变。' : notice }}</p>
    <template #footer><button type="submit" form="conversation-title-editor" class="primary" :disabled="!ready || saving || generating || !title.trim()">{{ saving ? '正在保存…' : '保存标题' }}</button></template>
  </ResourceDrawer>
</template>
