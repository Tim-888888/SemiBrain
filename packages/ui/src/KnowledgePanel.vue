<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, post } from './api'
import MarkdownAnswer from './MarkdownAnswer.vue'
const props = defineProps<{ manage: boolean }>()
const items = ref<any[]>([]), error = ref(''), busy = ref(false), preview = ref<any>(null), selected = ref<any>(null)
const path = ref(''), origin = ref('public'), external = ref(false), files = ref<File[]>([])
let timer: ReturnType<typeof setInterval>
async function load() { try { items.value = (await api('/v1/knowledge/documents')).items } catch (e) { error.value = (e as Error).message } }
function choose(event: Event) { files.value = Array.from((event.target as HTMLInputElement).files || []); if (files.value.length === 1) path.value = files.value[0].name }
async function upload() {
  busy.value = true; error.value = ''
  try {
    for (const file of files.value) {
      const body = new FormData()
      body.set('file', file); body.set('request_id', crypto.randomUUID()); body.set('document_path', files.value.length === 1 ? path.value : file.webkitRelativePath || file.name)
      body.set('data_origin', origin.value); body.set('allow_external', String(external.value)); body.set('visibility', 'demo')
      await api('/admin/v1/knowledge/uploads', { method: 'POST', body })
    }
    files.value = []; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function view(item: any) {
  selected.value = item; error.value = ''
  try { preview.value = await api(`/admin/v1/knowledge/documents/${item.id}/preview?version=${item.ingestion.version}`) }
  catch (e) { error.value = (e as Error).message }
}
async function publish(item: any) {
  try { await post(`/admin/v1/knowledge/documents/${item.id}/publish`, { request_id: crypto.randomUUID(), expected_revision: item.revision, version: item.ingestion.version }); preview.value = null; await load() }
  catch (e) { error.value = (e as Error).message }
}
async function unpublish(item: any) {
  try { await post(`/admin/v1/knowledge/documents/${item.id}/unpublish`, { request_id: crypto.randomUUID(), expected_revision: item.revision }); await load() }
  catch (e) { error.value = (e as Error).message }
}
const statusNames: Record<string, string> = { published: '已发布', unpublished: '已下架', staged: '待预览发布', queued: '等待解析', running: '处理中', failed: '处理失败', needs_attention: '需检查内容', receiving: '正在接收' }
onMounted(() => { load(); timer = setInterval(load, 5000) }); onUnmounted(() => clearInterval(timer))
</script>
<template>
  <section class="resource-panel">
    <div class="page-title"><div><p class="eyebrow">KNOWLEDGE LIBRARY</p><h1>知识库</h1><p class="muted">文档与来源放在一起，回答更容易核验。</p></div><span class="badge">{{ items.length }} 份资料</span></div>
    <div v-if="manage" class="upload-card">
      <h3>添加资料</h3><p class="muted small">支持 PDF、DOCX、Markdown、CSV，每个文件最大 32 MB。解析完成后预览并发布。</p>
      <input type="file" multiple accept=".pdf,.docx,.md,.csv" @change="choose" aria-label="选择知识库文件" />
      <label class="small">或选择文件夹<input type="file" webkitdirectory multiple @change="choose" aria-label="选择资料文件夹" /></label><div class="form-row"><label>文档路径<input v-model="path" placeholder="例如 工艺规范/文档名称.pdf" :disabled="files.length !== 1" /></label><label>资料来源<select v-model="origin"><option value="public">公开资料</option><option value="synthetic">合成演示资料</option><option value="authorized_business">已授权业务资料</option></select></label></div>
      <label class="checkbox"><input v-model="external" type="checkbox" />允许使用云端 MinerU 解析 PDF</label>
      <button class="primary" :disabled="!files.length || busy" @click="upload">{{ busy ? '正在上传…' : '上传并解析' }}</button>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <div v-if="!items.length" class="empty-card">知识库还没有资料。{{ manage ? '上传第一份文档，开始建立知识来源。' : '管理员发布资料后会显示在这里。' }}</div>
    <div class="document-list"><article v-for="item in items" :key="item.id" class="document-row"><span class="file-icon">▤</span><div class="document-detail"><strong>{{ item.title }}</strong><p class="muted small">{{ item.path }}</p><p v-if="item.ingestion?.error" class="error small">解析未完成，请检查文件或稍后重试。</p></div><span class="status-pill">{{ statusNames[item.ingestion?.status] || (item.active_version ? '已发布' : '未发布') }}</span><div v-if="manage" class="row-actions"><button v-if="['staged', 'published', 'needs_attention'].includes(item.ingestion?.status)" class="secondary" @click="view(item)">预览</button><button v-if="item.active_version" class="text-button" @click="unpublish(item)">下架</button></div></article></div>
    <div v-if="preview" class="modal-backdrop" @click.self="preview = null"><section class="preview-modal" role="dialog" aria-modal="true" aria-label="文档预览"><div class="page-title"><h2>{{ selected?.title }}</h2><button class="secondary" @click="preview = null">关闭</button></div><div class="preview-scroll"><MarkdownAnswer :text="preview.body_markdown" /><p v-if="preview.truncated" class="muted">预览仅显示部分内容，请核验原文件。</p><p v-if="preview.manifest?.quality_findings?.length" class="notice">内容提示：{{ preview.manifest.quality_findings.join('、') }}</p></div><div class="modal-footer"><span class="muted small">{{ preview.chunk_count }} 个内容片段</span><button v-if="selected?.ingestion?.status === 'staged'" class="primary" @click="publish(selected)">确认发布</button></div></section></div>
  </section>
</template>
