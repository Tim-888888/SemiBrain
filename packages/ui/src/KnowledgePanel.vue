<script setup lang="ts">
import { rememberFilters } from './page-filters'
import { documentCatalog } from './document-catalog'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, post } from './api'
import MarkdownAnswer from './MarkdownAnswer.vue'
import KnowledgeVersions from './KnowledgeVersions.vue'
import ResourceDrawer from './ResourceDrawer.vue'
const uploadOpen=ref(false), query=ref(''), state=ref(''), format=ref(''), page=ref(1), loading=ref(true), loadError=ref('')
const uploadResults=ref<{name:string;status:string;error?:string}[]>([])
const previewTab=ref('body'), chunks=ref<any[]>([]), chunksBusy=ref(false)
const previewOpen=computed({get:()=>!!preview.value,set:(value:boolean)=>{if(!value)preview.value=null}})
function documentFormat(item:any){return /\.([a-z0-9]{1,8})$/i.exec(item.path || item.title)?.[1].toLowerCase() || 'other'}
const formats=computed(()=>[...new Set(items.value.map(documentFormat))].sort())
const filtered=computed(()=>items.value.filter(item => (item.title+' '+item.path).toLowerCase().includes(query.value.toLowerCase()) && (!format.value || documentFormat(item)===format.value) && (!state.value || (state.value==='published' ? !!item.active_version : state.value==='unpublished' ? !item.active_version : ['queued','running','receiving'].includes(item.ingestion?.status)))))
const visible=computed(()=>filtered.value.slice((page.value-1)*25,page.value*25))
const pages=computed(()=>Math.max(1,Math.ceil(filtered.value.length/25)))
async function showChunks(){previewTab.value='chunks';chunksBusy.value=true;try{chunks.value=(await api(`/admin/v1/knowledge/documents/${selected.value.id}/versions/${preview.value.version}/chunks`)).items}catch(e){error.value=(e as Error).message}finally{chunksBusy.value=false}}

const versionDocument = ref<any>(null)
import { documentBundle, selectedDocuments } from './knowledge-upload.mjs'
const props = defineProps<{ manage: boolean }>()
const items = ref<any[]>([]), error = ref(''), busy = ref(false), preview = ref<any>(null), selected = ref<any>(null)
const path = ref(''), origin = ref('public'), external = ref(false), files = ref<File[]>([])
const changing = ref(''), notice = ref('')
const rebuilding = ref<any>(null), reviewText = ref(''), reviewReason = ref(''), reviewing = ref(false)
let timer: ReturnType<typeof setInterval>
async function load() { try { items.value = (await documentCatalog()).items; loadError.value = '' } catch (e) { loadError.value = (e as Error).message } finally { loading.value=false } }
function choose(event: Event) { files.value = Array.from((event.target as HTMLInputElement).files || []); (event.target as HTMLInputElement).value=''; if (files.value.length === 1) path.value = files.value[0].name }
async function upload() {
  busy.value = true; error.value = ''; uploadResults.value=[]
  try {
    const documents = await selectedDocuments(files.value)
    if (!documents.length) throw new Error('没有选中支持的知识文档。')
    for (const file of documents) {
      const row={name:file.name,status:'正在上传',error:''}; uploadResults.value.push(row)
      const progress=uploadResults.value[uploadResults.value.length-1]
      try {
      const documentPath = files.value.length === 1 ? path.value : file.webkitRelativePath || file.name
      const images = await documentBundle(file, files.value, documentPath)
      const body = new FormData()
      body.set('file', file); body.set('request_id', crypto.randomUUID()); body.set('document_path', documentPath)
      body.set('image_paths', JSON.stringify(images.map(image => image.path)))
      for (const image of images) body.append('images', image.file)
      body.set('data_origin', origin.value); body.set('allow_external', String(external.value)); body.set('visibility', 'demo')
      await api('/admin/v1/knowledge/uploads', { method: 'POST', body }); progress.status='已提交解析'
      } catch(e) {progress.status='上传失败';progress.error=(e as Error).message}
    }
    const count=uploadResults.value.filter(row=>row.status==='上传失败').length
    files.value=[];notice.value=count ? `已处理 ${documents.length} 份资料，其中 ${count} 份上传失败，请查看上传任务记录并单独重选失败文件。` : `已提交 ${documents.length} 份资料，解析完成后请预览并发布。`; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function view(item: any, version = item.ingestion?.version || item.active_version) {
  selected.value = item; previewTab.value='body';chunks.value=[];error.value = ''; reviewing.value = false; reviewReason.value = ''
  try { preview.value = { ...await api(`/admin/v1/knowledge/documents/${item.id}/preview?version=${version}`), version } }
  catch (e) { error.value = (e as Error).message }
}
function beginReview() { reviewText.value=preview.value.body_markdown;reviewing.value=true }
async function submitReview() {
  if (changing.value) return
  changing.value=selected.value.id;error.value=''
  try {
    await post(`/admin/v1/knowledge/documents/${selected.value.id}/review`,{request_id:crypto.randomUUID(),expected_revision:selected.value.revision,source_version:preview.value.version,text:reviewText.value,reason:reviewReason.value})
    preview.value=null;notice.value='复核内容已提交为新版本，重建完成后请再次预览发布。';await load()
  } catch(e) { error.value=(e as Error).message }
  finally { changing.value='' }
}
async function cancelParsing(item:any) {
  if (changing.value) return
  changing.value=item.id;error.value=''
  try { await post(`/admin/v1/knowledge/jobs/${item.ingestion.id}/cancel`,{});notice.value='已请求停止处理，当前已发布版本继续可用。';await load() }
  catch(e) { error.value=(e as Error).message }
  finally { changing.value='' }
}
const findingNames:Record<string,string>={FORMULA_CACHE_MISSING:'公式没有缓存结果，未代为计算',LEGACY_XLS_VALUES_ONLY:'旧版表格仅取得数值，公式和图片需复核',EMBEDDED_IMAGE_UNSUPPORTED:'部分内嵌图片无法解析',EMBEDDED_OBJECT_REQUIRES_REVIEW:'图表或内嵌对象需核对原文件',XMIND_ATTACHMENTS_REQUIRE_REVIEW:'思维导图附件需核对原文件',XMIND_NON_TREE_CONTENT_REQUIRES_REVIEW:'思维导图含非树状内容，需核对原文件',IMAGE_TRANSCRIPTION_REVIEW_REQUIRED:'图片转录文字需核对原图',OCR_REQUIRED:'图片缺少可读文字，请补充转录',XLSX_PACKAGING_REPAIRED:'已修复表格封装，请核对内容',XLSX_STYLES_REPAIRED:'已修复表格样式，请核对内容'}
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
const statusNames: Record<string, string> = { published: '已发布', unpublished: '已下架', staged: '待预览发布', queued: '等待解析', running: '处理中', failed: '处理失败', needs_attention: '需检查内容', receiving: '正在接收', cancelled: '已取消' }
watch([query,state,format],()=>{page.value=1})
watch(pages,value=>{page.value=Math.min(page.value,value)})
onMounted(() => { load(); timer = setInterval(load, 5000) }); onUnmounted(() => clearInterval(timer))
rememberFilters('KnowledgePanel', {query,state,format})
</script>
<template>
  <section class="resource-panel">
    <div class="page-title"><div><p class="eyebrow">KNOWLEDGE LIBRARY</p><h1>知识库</h1><p class="muted">文档与来源放在一起，回答更容易核验。</p></div><button v-if="manage" class="primary" :disabled="busy" @click="uploadOpen=true">＋ 上传文档</button><span v-else class="badge">{{ items.length }} 份资料</span></div>
    <ResourceDrawer v-model="uploadOpen" title="上传知识资料" :busy="busy" :snapshot="{files: files.map(f=>({name:f.name,size:f.size})),path,origin,external}" :error="error">
      <h3>添加资料</h3><p class="muted small">支持 PDF、DOC/DOCX、XLS/XLSX、PPT/PPTX、Markdown、CSV、JSON、XMind、EPUB、HTML/MHTML 和图片。含图片的 Markdown 请连同配图选择文件夹，单篇及配图合计最大 32 MB。解析完成后预览并发布。</p>
      <input type="file" multiple accept=".pdf,.doc,.docx,.ppt,.md,.csv,.xlsx,.xls,.pptx,.epub,.xmind,.json,.html,.htm,.mhtml,.mht,.png,.jpg,.jpeg,.webp,.gif,.svg" @change="choose" aria-label="选择知识库文件" />
      <label class="small">或选择文件夹<input type="file" webkitdirectory multiple @change="choose" aria-label="选择资料文件夹" /></label><div class="form-row"><label>文档路径<input v-model="path" placeholder="例如 工艺规范/文档名称.pdf" :disabled="files.length !== 1" /></label><label>资料来源<select v-model="origin"><option value="public">公开资料</option><option value="synthetic">合成演示资料</option><option value="authorized_business">已授权业务资料</option></select></label></div>
      <label class="checkbox"><input v-model="external" type="checkbox" />允许使用云端解析 PDF 和转录图片文字</label>
      <ul v-if="uploadResults.length" class="upload-results"><li v-for="(row,i) in uploadResults" :key="i"><strong>{{row.name}}</strong> · {{row.status}}<p v-if="row.error" class="error">{{row.error}}</p></li></ul>
      <template #footer><button class="primary" :disabled="!files.length || busy" @click="upload">{{busy ? '正在上传…' : '上传并解析'}}</button></template></ResourceDrawer>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status" class="notice">{{ notice }}</p>
    <p v-if="loadError" class="error" role="alert">{{loadError}} <button class="text-button" @click="load">重新加载</button></p><div class="page-toolbar"><input v-model="query" type="search" placeholder="搜索文档名称或路径" aria-label="搜索文档"/><select v-model="state" aria-label="发布状态"><option value="">全部状态</option><option value="published">已发布</option><option value="unpublished">未发布</option><option value="processing">处理中</option></select><select v-model="format" aria-label="文档格式"><option value="">全部格式</option><option v-for="type in formats" :key="type" :value="type">{{type==='other'?'其他':type.toUpperCase()}}</option></select><span class="result-count">{{filtered.length}} / {{items.length}} 份可访问资料</span></div>
    <div v-if="loading" class="loading-state">正在加载资料目录…</div><div v-else-if="items.length && !filtered.length" class="empty-card">没有匹配的文档，请调整关键词或筛选条件。</div>
    <div v-else-if="!items.length && !error" class="empty-card">知识库还没有资料。{{ manage ? '上传第一份文档，开始建立知识来源。' : '管理员发布资料后会显示在这里。' }}</div>
    <div class="document-list">
      <article v-for="item in visible" :key="item.id" class="document-row">
        <span class="file-icon">▤</span>
        <div class="document-detail"><strong>{{ item.title }}</strong><p class="muted small">{{ item.path }}</p>
          <p v-if="draftStatus(item)" class="muted small">{{ draftStatus(item) }}</p>
          <p v-if="item.ingestion?.error" class="error small">新版本处理未完成，可重新处理。{{ item.active_version ? '当前已发布版本仍可使用。' : '' }}</p>
        </div>
        <span class="status-pill">{{ item.active_version ? '已发布' : statusNames[item.ingestion?.status] || '未发布' }}</span>
        <div v-if="manage" class="row-actions"><button v-if="['queued','running'].includes(item.ingestion?.status)" class="secondary" :disabled="!!changing" @click="cancelParsing(item)">取消处理</button>
          <button v-if="item.active_version || item.restore_version" class="secondary" @click="versionDocument = item">修订／版本历史</button>
          <button v-if="item.active_version" class="secondary" @click="view(item, item.active_version)">预览当前版本</button>
          <button v-if="['staged', 'unpublished', 'needs_attention'].includes(item.ingestion?.status) && item.ingestion?.version !== item.active_version" class="secondary" @click="view(item)">{{ item.active_version ? '预览新版本' : '预览' }}</button>
          <details class="row-menu"><summary>更多操作</summary><div class="row-menu-body"><button v-if="item.reprocess_source_version" class="secondary" :disabled="!!changing || item.reprocess_pending" @click="openRebuild(item)">重新处理／重建版本</button>
          <button v-if="item.active_version" class="text-button" :disabled="!!changing" @click="unpublish(item)">{{ changing === item.id ? '处理中…' : '下架' }}</button>
          <button v-else-if="item.restore_version" class="secondary" :disabled="!!changing || item.reprocess_pending" @click="changePublication(item, 'republish')">{{ changing === item.id ? '正在校验并上架…' : '重新上架' }}</button></div></details>
        </div>
      </article>
    </div>
    <div class="pagination"><span class="muted small">第 {{page}} / {{pages}} 页 · 按全部已加载资料筛选</span><button class="secondary" :disabled="page<=1" @click="page--">上一页</button><button class="secondary" :disabled="page>=pages" @click="page++">下一页</button></div>
    <KnowledgeVersions v-if="versionDocument" :document="versionDocument" @close="versionDocument = null" @changed="load" />
    <ResourceDrawer v-model="previewOpen" :title="selected?.title || '文档预览'" width="1000px" :busy="!!changing || chunksBusy" :snapshot="reviewing ? {reviewText,reviewReason} : null" :error="error"><template v-if="preview"><div class="tabs"><button :aria-selected="previewTab==='body'" @click="previewTab='body'">正文与图片</button><button :aria-selected="previewTab==='chunks'" @click="showChunks">切片与来源</button></div><div v-if="previewTab==='body'"><MarkdownAnswer :text="preview.body_markdown" :image-refs="preview.image_refs" /><p v-if="preview.truncated" class="muted">预览仅显示部分内容，请核验原文件。</p><p v-if="preview.manifest?.quality_findings?.length" class="notice">内容提示：{{ preview.manifest.quality_findings.map((v:string)=>findingNames[v] || v).join('；') }}</p></div><div v-if="previewTab==='chunks'"><p v-if="chunksBusy">正在加载切片…</p><article v-for="(chunk,i) in chunks" :key="chunk.id" class="panel-card"><h3>片段 {{i+1}} · {{chunk.context_header || '正文'}}</h3><p class="small muted">{{chunk.chunk_type || chunk.kind || '内容片段'}}<span v-if="chunk.parent_id"> · 父片段 {{chunk.parent_id}}</span></p><MarkdownAnswer :text="chunk.text" :image-refs="chunk.image_refs"/><details><summary>片段编号与关系</summary><pre>{{JSON.stringify({id:chunk.id,parent_id:chunk.parent_id,previous_id:chunk.previous_id,next_id:chunk.next_id},null,2)}}</pre></details></article></div><form v-if="reviewing" @submit.prevent="submitReview"><label>核对原文件并修正正文<textarea v-model="reviewText" rows="12" maxlength="200000" required style="width:100%" /></label><label>复核依据与接受的限制<input v-model="reviewReason" minlength="10" maxlength="1000" required /></label><button class="primary" :disabled="!!changing">保存复核版本</button></form><div class="modal-footer"><button v-if="preview.manifest?.status==='needs_attention' && !preview.truncated && !reviewing" class="secondary" @click="beginReview">核对并修正内容</button><span class="muted small">{{ preview.chunk_count }} 个内容片段</span><button v-if="selected?.ingestion?.status === 'staged' && preview.version === selected?.ingestion?.version" class="primary" :disabled="!!changing" @click="publish(selected)">{{ selected?.active_version ? '发布新版本' : '确认发布' }}</button><button v-else-if="!selected?.active_version && selected?.restore_version === preview.version" class="primary" :disabled="!!changing" @click="changePublication(selected, 'republish')">{{ changing ? '正在校验并上架…' : '重新上架' }}</button></div></template></ResourceDrawer>
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
.document-row .row-actions { flex-wrap: wrap; justify-content: flex-end; max-width: 46%; }.document-list{overflow:visible;background:white}.pagination{display:flex;justify-content:flex-end;align-items:center;gap:12px;margin:18px 0}.row-menu{position:relative}.row-menu summary{list-style:none;font-size:12px;padding:7px!important;color:var(--accent);white-space:nowrap}.row-menu-body{position:absolute;right:0;top:100%;background:white;border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 25px #1d35251a;padding:10px;z-index:3;display:grid;gap:6px;min-width:190px}.upload-results{padding-left:20px}
@media (max-width: 650px) {
  .document-row .row-actions { width: 100%; max-width: none; }
}
</style>
