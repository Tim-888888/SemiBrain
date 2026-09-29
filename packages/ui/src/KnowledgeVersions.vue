<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from './api'
const props = defineProps<{ document: any }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const list = ref<any>(null), version = ref(''), chunks = ref<any[]>([]), chunk = ref<any>(null)
const edited = ref(''), diff = ref(''), error = ref(''), notice = ref(''), busy = ref(false)
async function load() {
  list.value = await api(`/admin/v1/knowledge/documents/${props.document.id}/versions`)
  if (!version.value) version.value = list.value.active_version || list.value.items[0]?.version || ''
  await select()
}
async function select() {
  busy.value = true; error.value = ''; chunk.value = null; chunks.value = []; diff.value = ''
  try {
    if (!version.value) return
    chunks.value = (await api(`/admin/v1/knowledge/documents/${props.document.id}/versions/${version.value}/chunks`)).items
    diff.value = (await api(`/admin/v1/knowledge/documents/${props.document.id}/versions/${version.value}/diff`)).diff || ''
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function choose(item: any) { chunk.value = item; edited.value = item.text; notice.value = '' }
async function save() {
  busy.value = true; error.value = ''
  try {
    await post(`/admin/v1/knowledge/documents/${props.document.id}/edit`, { request_id: crypto.randomUUID(),
      expected_revision: list.value.revision, source_version: version.value, chunk_id: chunk.value.id,
      expected_hash: chunk.value.content_hash, text: edited.value })
    notice.value = '修订已提交，正在建立新版本索引。当前发布版本继续可用；请回知识库预览并发布新版本。'
    chunk.value = null; emit('changed'); await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function rollback() {
  busy.value = true; error.value = ''
  try {
    await post(`/admin/v1/knowledge/documents/${props.document.id}/rollback`, { request_id: crypto.randomUUID(),
      expected_revision: list.value.revision, version: version.value })
    notice.value = '已切换到所选历史发布版本。'; emit('changed'); await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
onMounted(async () => { try { await load() } catch (e) { error.value = (e as Error).message } })
</script>
<template>
  <div class="modal-backdrop"><section class="diagnostics-modal" role="dialog" aria-modal="true" aria-label="文档修订与历史版本">
    <div class="page-title"><h2>{{ document.title }} · 修订与版本</h2><button class="secondary" :disabled="busy" @click="emit('close')">关闭</button></div>
    <p class="muted">修改内容会生成新的待审核版本，并重建片段关系与检索索引。可回滚到曾经发布且资产完整的版本。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="list"><div class="row-actions"><select v-model="version" :disabled="busy" @change="select" aria-label="文档版本"><option v-for="item in list.items" :key="item.version" :value="item.version">第 {{ item.generation }} 代 · {{ item.created_at }} {{ item.version === list.active_version ? '（当前）' : item.published ? '（历史发布）' : '（草稿）' }}</option></select><button v-if="version !== list.active_version && list.items.some((item: any) => item.version === version && item.published && item.ready)" class="secondary" :disabled="busy" @click="rollback">回滚到此版本</button></div>
    <details v-if="diff"><summary>修订差异</summary><pre class="revision-diff">{{ diff }}</pre></details>
    <div class="revision-layout"><div class="revision-chunks"><button v-for="(item, index) in chunks" :key="item.id" class="secondary" @click="choose(item)" :disabled="busy">片段 {{ index + 1 }} · {{ item.context_header || item.text.slice(0, 60) }}</button></div>
      <div v-if="chunk"><label>片段内容（Markdown）<textarea v-model="edited" rows="18" :disabled="busy || version !== list.active_version" /></label><p class="small muted">保留已有图片引用即可继续显示图片；新增图片请通过资料上传入口添加。</p><button v-if="version === list.active_version" class="primary" :disabled="busy || !edited.trim() || edited === chunk.text" @click="save">保存修订并重建</button></div>
    </div></template>
  </section></div>
</template>
<style scoped>
.revision-layout { display: grid; grid-template-columns: minmax(150px, 25%) 1fr; gap: 20px; margin-top: 20px; }
.revision-chunks { display: flex; flex-direction: column; gap: 8px; max-height: 60vh; overflow: auto; }
textarea { display: block; width: 100%; font: 14px/1.6 monospace; resize: vertical; }
.revision-diff { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 40vh; overflow: auto; }
@media (max-width: 650px) { .revision-layout { grid-template-columns: 1fr; } .revision-chunks { max-height: 20vh; } }
</style>
