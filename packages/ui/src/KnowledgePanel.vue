<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, post } from './api'
import MarkdownAnswer from './MarkdownAnswer.vue'
import { documentBundle, documentFile } from './knowledge-upload.mjs'
const props = defineProps<{ manage: boolean }>()
const items = ref<any[]>([]), error = ref(''), busy = ref(false), preview = ref<any>(null), selected = ref<any>(null)
const path = ref(''), origin = ref('public'), external = ref(false), files = ref<File[]>([])
const changing = ref(''), notice = ref('')
const rebuilding = ref<any>(null)
let timer: ReturnType<typeof setInterval>
async function load() { try { items.value = (await api('/v1/knowledge/documents')).items } catch (e) { error.value = (e as Error).message } }
function choose(event: Event) { files.value = Array.from((event.target as HTMLInputElement).files || []); if (files.value.length === 1) path.value = files.value[0].name }
async function upload() {
  busy.value = true; error.value = ''
  try {
    const documents = files.value.filter(documentFile)
    if (!documents.length) throw new Error('没有选中支持的知识文档。图片需与 Markdown 一起选择。')
    for (const file of documents) {
      const documentPath = files.value.length === 1 ? path.value : file.webkitRelativePath || file.name
      const images = await documentBundle(file, files.value, documentPath)
      const body = new FormData()
      body.set('file', file); body.set('request_id', crypto.randomUUID()); body.set('document_path', documentPath)
      body.set('image_paths', JSON.stringify(images.map(image => image.path)))
      for (const image of images) body.append('images', image.file)
      body.set('data_origin', origin.value); body.set('allow_external', String(external.value)); body.set('visibility', 'demo')
      await api('/admin/v1/knowledge/uploads', { method: 'POST', body })
    }
    files.value = []; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function view(item: any, version = item.ingestion?.version || item.active_version) {
  selected.value = item; error.value = ''
  try { preview.value = { ...await api(`/admin/v1/knowledge/documents/${item.id}/preview?version=${version}`), version } }
  catch (e) { error.value = (e as Error).message }
}
async function publish(item: any) {
  await changePublication(item, 'publish')
}
async function unpublish(item: any) {
  await changePublication(item, 'unpublish')
}
async function changePublication(item: any, action: 'publish' | 'unpublish' | 'republish') {
  if (changing.value) return
  changing.value = item.id; error.value = ''; notice.value = ''
  try {
    await post(`/admin/v1/knowledge/documents/${item.id}/${action}`, {
      request_id: crypto.randomUUID(), expected_revision: item.revision,
      ...(action === 'publish' ? { version: preview.value?.version || item.ingestion.version } : {}),
    })
    preview.value = null
    notice.value = action === 'unpublish' ? '文档已下架，后续知识库检索将排除这份文档。'
      : action === 'republish' ? '文档已重新上架，可以继续用于知识库检索。' : '文档已发布。'
    await load()
  } catch (e) { error.value = (e as Error).message; await load() }
  finally { changing.value = '' }
}
function openRebuild(item: any) {
  rebuilding.value = { item, command: { request_id: crypto.randomUUID(), expected_revision: item.revision, source_version: item.reprocess_source_version } }
}
async function rebuild() {
  if (!rebuilding.value || changing.value) return
  const { item, command } = rebuilding.value
  changing.value = item.id; error.value = ''; notice.value = ''
  try {
    await post(`/admin/v1/knowledge/documents/${item.id}/reprocess`, command)
    rebuilding.value = null
    notice.value = '已开始重建新版本。处理完成后请预览并确认发布；已发布版本继续可用。'
    await load()
  } catch (e) { error.value = (e as Error).message; await load() }
  finally { changing.value = '' }
}
function draftStatus(item: any) {
  return item.active_version && item.ingestion?.version !== item.active_version
    ? `新版本：${statusNames[item.ingestion?.status] || '处理中'}` : ''
}
const statusNames: Record<string, string> = { published: '已发布', unpublished: '已下架', staged: '待预览发布', queued: '等待解析', running: '处理中', failed: '处理失败', needs_attention: '需检查内容', receiving: '正在接收' }
onMounted(() => { load(); timer = setInterval(load, 5000) }); onUnmounted(() => clearInterval(timer))
</script>
<template>
  <section class="resource-panel">
    <div class="page-title"><div><p class="eyebrow">KNOWLEDGE LIBRARY</p><h1>知识库</h1><p class="muted">文档与来源放在一起，回答更容易核验。</p></div><span class="badge">{{ items.length }} 份资料</span></div>
    <div v-if="manage" class="upload-card">
      <h3>添加资料</h3><p class="muted small">支持 PDF、DOCX、Markdown、CSV。含图片的 Markdown 请连同配图选择文件夹，单篇及配图合计最大 32 MB。解析完成后预览并发布。</p>
      <input type="file" multiple accept=".pdf,.docx,.md,.csv,.png,.jpg,.jpeg,.webp,.gif,.svg" @change="choose" aria-label="选择知识库文件" />
      <label class="small">或选择文件夹<input type="file" webkitdirectory multiple @change="choose" aria-label="选择资料文件夹" /></label><div class="form-row"><label>文档路径<input v-model="path" placeholder="例如 工艺规范/文档名称.pdf" :disabled="files.length !== 1" /></label><label>资料来源<select v-model="origin"><option value="public">公开资料</option><option value="synthetic">合成演示资料</option><option value="authorized_business">已授权业务资料</option></select></label></div>
      <label class="checkbox"><input v-model="external" type="checkbox" />允许使用云端 MinerU 解析 PDF</label>
      <button class="primary" :disabled="!files.length || busy" @click="upload">{{ busy ? '正在上传…' : '上传并解析' }}</button>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status" class="notice">{{ notice }}</p>
    <div v-if="!items.length" class="empty-card">知识库还没有资料。{{ manage ? '上传第一份文档，开始建立知识来源。' : '管理员发布资料后会显示在这里。' }}</div>
    <div class="document-list">
      <article v-for="item in items" :key="item.id" class="document-row">
        <span class="file-icon">▤</span>
        <div class="document-detail"><strong>{{ item.title }}</strong><p class="muted small">{{ item.path }}</p>
          <p v-if="draftStatus(item)" class="muted small">{{ draftStatus(item) }}</p>
          <p v-if="item.ingestion?.error" class="error small">新版本处理未完成，可重新处理。{{ item.active_version ? '当前已发布版本仍可使用。' : '' }}</p>
        </div>
        <span class="status-pill">{{ item.active_version ? '已发布' : statusNames[item.ingestion?.status] || '未发布' }}</span>
        <div v-if="manage" class="row-actions">
          <button v-if="item.active_version" class="secondary" @click="view(item, item.active_version)">预览当前版本</button>
          <button v-if="['staged', 'unpublished', 'needs_attention'].includes(item.ingestion?.status) && item.ingestion?.version !== item.active_version" class="secondary" @click="view(item)">{{ item.active_version ? '预览新版本' : '预览' }}</button>
          <button v-if="item.reprocess_source_version" class="secondary" :disabled="!!changing || item.reprocess_pending" @click="openRebuild(item)">重新处理／重建版本</button>
          <button v-if="item.active_version" class="text-button" :disabled="!!changing" @click="unpublish(item)">{{ changing === item.id ? '处理中…' : '下架' }}</button>
          <button v-else-if="item.restore_version" class="secondary" :disabled="!!changing || item.reprocess_pending" @click="changePublication(item, 'republish')">{{ changing === item.id ? '正在校验并上架…' : '重新上架' }}</button>
        </div>
      </article>
    </div>
    <div v-if="preview" class="modal-backdrop" @click.self="preview = null"><section class="preview-modal" role="dialog" aria-modal="true" aria-label="文档预览"><div class="page-title"><h2>{{ selected?.title }}</h2><button class="secondary" @click="preview = null">关闭</button></div><div class="preview-scroll"><MarkdownAnswer :text="preview.body_markdown" :image-refs="preview.image_refs" /><p v-if="preview.truncated" class="muted">预览仅显示部分内容，请核验原文件。</p><p v-if="preview.manifest?.quality_findings?.length" class="notice">内容提示：{{ preview.manifest.quality_findings.join('、') }}</p></div><div class="modal-footer"><span class="muted small">{{ preview.chunk_count }} 个内容片段</span><button v-if="selected?.ingestion?.status === 'staged' && preview.version === selected?.ingestion?.version" class="primary" :disabled="!!changing" @click="publish(selected)">{{ selected?.active_version ? '发布新版本' : '确认发布' }}</button><button v-else-if="!selected?.active_version && selected?.restore_version === preview.version" class="primary" :disabled="!!changing" @click="changePublication(selected, 'republish')">{{ changing ? '正在校验并上架…' : '重新上架' }}</button></div></section></div>
    <div v-if="rebuilding" class="modal-backdrop" @click.self="!changing && (rebuilding = null)">
      <section class="preview-modal" role="dialog" aria-modal="true" aria-label="重新处理文档">
        <h2>重新处理／重建版本</h2><p>{{ rebuilding.item.title }}</p>
        <p>使用保留的原文件和配图，按当前规则重新解析并建立检索内容。无需再次上传。</p>
        <p>新版本处理完成后，由你预览并确认发布。{{ rebuilding.item.active_version ? '期间当前已发布版本继续可用。' : '处理完成不会自动上架。' }}</p>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
        <div class="modal-footer"><button class="secondary" :disabled="!!changing" @click="rebuilding = null">取消</button><button class="primary" :disabled="!!changing" @click="rebuild">{{ changing ? '正在提交…' : '开始重建' }}</button></div>
      </section>
    </div>
  </section>
</template>
<style scoped>
.document-row .row-actions { flex-wrap: wrap; justify-content: flex-end; max-width: 46%; }
@media (max-width: 650px) {
  .document-row .row-actions { width: 100%; max-width: none; }
}
</style>
