<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, post } from './api'
const props = defineProps<{ runId: string; automatic?: { status: string; format: string } | null }>()
type ExportRow = { export_id: string; format: string; status: string; asset_id?: string; filename?: string; error?: string; expires_at: string }
const open = ref(false), busy = ref(false), error = ref(''), rows = ref<ExportRow[]>([])
const automatic = ref(props.automatic)
const names: Record<string, string> = { md: 'Markdown', docx: 'Word', pdf: 'PDF' }
const states: Record<string, string> = { queued: '等待转换', running: '正在转换', succeeded: '可下载', failed: '转换未完成' }
const failures: Record<string, string> = {
  EXPORT_IMAGE_UNREGISTERED: '本次导出未能匹配回答中的图片。请重新生成；若仍出现此提示，请反馈图片引用问题。',
  EXPORT_IMAGE_REFERENCE_UNSUPPORTED: '回答中的图片引用格式暂不支持导出，请反馈此问题。',
  EXPORT_IMAGES_TOO_LARGE: '图片数量或大小超过导出限制，请减少配图后重新生成。',
  ASSET_NOT_FOUND: '源图片已不存在，请重新生成相关图表或回答。',
  ASSET_UNAVAILABLE: '源文件当前无权访问，请检查来源状态或联系管理员。',
  ASSET_VERSION_UNAVAILABLE: '源图片版本已不可用，请重新查询资料并生成回答。',
  EXPORT_EXPIRED: '导出文件已过期，请重新生成。',
  EXPORT_OWNER_REVOKED: '当前账号权限已变化，请重新登录后检查访问权限。',
  EXPORT_SOURCE_CHANGED: '回答来源已变化，请刷新页面后重新导出。',
}
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false, until = 0
async function load() {
  try {
    const result = await api('/v1/runs/' + props.runId + '/exports')
    if (disposed) return
    error.value = ''; rows.value = result.items
    automatic.value = result.automatic || automatic.value
    clearTimeout(timer)
    if (Date.now() < until && (rows.value.some(row => ['queued', 'running'].includes(row.status)) || (automatic.value && ['pending', 'queued', 'running'].includes(automatic.value.status)))) timer = setTimeout(load, 3000)
  } catch (e) {
    if (!disposed) {
      error.value = (e as Error).message
      clearTimeout(timer)
      if (Date.now() < until) timer = setTimeout(load, 5000)
    }
  }
}
async function show() { open.value = !open.value; if (open.value) { until = Math.max(until, Date.now() + 90000); await load() } }
async function create(format: string) {
  busy.value = true; error.value = ''
  try {
    await post('/v1/runs/' + props.runId + '/exports', { request_id: crypto.randomUUID(), format })
    until = Date.now() + 90000; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
onUnmounted(() => { disposed = true; clearTimeout(timer) })
onMounted(() => { if (props.automatic) { open.value = true; until = Date.now() + 660000; void load() } })
</script>
<template>
  <div class="report-export">
    <button class="text-button" :aria-expanded="open" @click="show">{{ automatic ? '↓ 文件交付与导出' : '↓ 导出回答' }}</button>
    <div v-if="open" class="export-panel">
      <div class="row-actions"><button v-for="(label, format) in names" :key="format" class="secondary" :disabled="busy" @click="create(String(format))">生成 {{ label }}</button><button class="text-button" :disabled="busy" @click="load">刷新状态</button></div>
      <p v-if="automatic && ['pending', 'queued', 'running'].includes(automatic.status)" role="status">正在生成你要求的 Markdown 文件…</p>
      <p v-if="automatic?.status === 'failed'" role="alert" class="error">自动文件交付未完成，正文已保留。可以点击“生成 Markdown”重试。</p>
      <p class="small muted">文件保留 30 天。含图片的 Markdown 下载为 ZIP，解压后打开其中的 MD 即可离线看图；无图时直接下载 MD。Word/PDF 包含配图，公式保留原始文本。</p>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <div v-for="row in rows" :key="row.export_id" class="export-row">
        <span>{{ names[row.format] }} · {{ states[row.status] || row.status }}</span>
        <a v-if="row.status === 'succeeded' && row.asset_id" :href="'/v1/assets/' + row.asset_id + '/content'" :download="row.filename">下载 {{ row.filename }}</a>
        <span v-if="row.status === 'failed'" class="small muted">{{ failures[row.error || ''] || '转换未完成，请稍后重试；若持续失败，请反馈此问题。' }}</span>
      </div>
    </div>
  </div>
</template>
<style scoped>
.report-export{margin-top:4px}.export-panel{padding:16px;border:1px solid var(--border,#dce6df);border-radius:10px;margin:8px 0;line-height:1.6}.export-row{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0}.export-panel .row-actions{flex-wrap:wrap}
</style>
