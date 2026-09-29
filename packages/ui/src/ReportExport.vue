<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { api, post } from './api'
const props = defineProps<{ runId: string }>()
type ExportRow = { export_id: string; format: string; status: string; asset_id?: string; filename?: string; error?: string; expires_at: string }
const open = ref(false), busy = ref(false), error = ref(''), rows = ref<ExportRow[]>([])
const names: Record<string, string> = { md: 'Markdown', docx: 'Word', pdf: 'PDF' }
const states: Record<string, string> = { queued: '等待转换', running: '正在转换', succeeded: '可下载', failed: '转换未完成' }
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false, until = 0
async function load() {
  try {
    const result = await api('/v1/runs/' + props.runId + '/exports')
    if (disposed) return
    rows.value = result.items
    clearTimeout(timer)
    if (Date.now() < until && rows.value.some(row => ['queued', 'running'].includes(row.status))) timer = setTimeout(load, 3000)
  } catch (e) { if (!disposed) error.value = (e as Error).message }
}
async function show() { open.value = !open.value; if (open.value) { until = Date.now() + 90000; await load() } }
async function create(format: string) {
  busy.value = true; error.value = ''
  try {
    await post('/v1/runs/' + props.runId + '/exports', { request_id: crypto.randomUUID(), format })
    until = Date.now() + 90000; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>
<template>
  <div class="report-export">
    <button class="text-button" :aria-expanded="open" @click="show">↓ 导出回答</button>
    <div v-if="open" class="export-panel">
      <div class="row-actions"><button v-for="(label, format) in names" :key="format" class="secondary" :disabled="busy" @click="create(String(format))">生成 {{ label }}</button><button class="text-button" :disabled="busy" @click="load">刷新状态</button></div>
      <p class="small muted">转换当前已保存的回答，文件保留 30 天。Word/PDF 包含正文中已登记的配图；Markdown 图片需登录后查看。公式保留原始文本。</p>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <div v-for="row in rows" :key="row.export_id" class="export-row">
        <span>{{ names[row.format] }} · {{ states[row.status] || row.status }}</span>
        <a v-if="row.status === 'succeeded' && row.asset_id" :href="'/v1/assets/' + row.asset_id + '/content'" :download="row.filename">下载 {{ row.filename }}</a>
        <span v-if="row.status === 'failed'" class="small muted">请重试；若来源已不可用，请重新调查。</span>
      </div>
    </div>
  </div>
</template>
<style scoped>
.report-export{margin-top:4px}.export-panel{padding:16px;border:1px solid var(--border,#dce6df);border-radius:10px;margin:8px 0;line-height:1.6}.export-row{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0}.export-panel .row-actions{flex-wrap:wrap}
</style>
