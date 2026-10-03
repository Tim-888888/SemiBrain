<script setup lang="ts">
import { rememberFilters } from './page-filters'
import { documentCatalog } from './document-catalog'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, post } from './api'
import MarkdownAnswer from './MarkdownAnswer.vue'
import KnowledgeVersions from './KnowledgeVersions.vue'
import ResourceDrawer from './ResourceDrawer.vue'
const loading=ref(true)
const query=ref(''), sourceQuery=ref(''), previewMode=ref(false)
const filtered=computed(()=>items.value.filter(item=>item.title.toLowerCase().includes(query.value.toLowerCase())))
const readable=computed({get:()=>!!selected.value && !editing.value,set:(open:boolean)=>{if(!open)selected.value=null}})
const reader=ref<HTMLElement|null>(null)
const headings=computed(()=>{let fence=false;return String(selected.value?.body_markdown || '').split('\n').filter(line=>{if(/^\s*(```|~~~)/.test(line)){fence=!fence;return false}return !fence && /^#{1,6}\s+/.test(line)}).map(line=>line.replace(/^#+\s+/,''))})
function jump(i:number){reader.value?.querySelectorAll('h1,h2,h3,h4,h5,h6')[i]?.scrollIntoView({block:'start'})}
const statusNames:Record<string,string>={staged:'待发布',published:'已发布',running:'处理中',queued:'排队中',failed:'处理失败',unpublished:'已下架',needs_attention:'需检查内容',cancelled:'已取消'}

const props = defineProps<{ manage: boolean }>()
const items = ref<any[]>([]), sources = ref<any[]>([]), selected = ref<any>(null), history = ref<any>(null)
const error = ref(''), notice = ref(''), busy = ref(false), editing = ref(false), editor = ref<any>({})
const draftRun = ref(''), generating = ref(false)
let timer: ReturnType<typeof setInterval>
let generationTimer: ReturnType<typeof setInterval> | undefined
async function load() {
  try {
    const [pages, docs] = await Promise.all([api('/v1/wiki/pages'),documentCatalog()])
    items.value = pages.items; sources.value = docs.items.filter((d: any) => d.active_version && d.kind !== 'wiki')
  } catch (e) { error.value = (e as Error).message } finally{loading.value=false}
}
function create() {
  editor.value = { title:'', body_markdown:'', applicability:'', visibility:'demo', document_id:null,
    expected_revision:0, source_ids:[], source_refs:[], expires:'', request_id:crypto.randomUUID() }
  editing.value = true; selected.value = null; error.value = ''; notice.value = ''
}
async function view(item: any, draft = false) {
  busy.value = true; error.value = ''
  try { selected.value = await api(draft ? `/admin/v1/wiki/pages/${item.id}/revisions/${item.ingestion.version}` : `/v1/wiki/pages/${item.id}`) }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function edit() {
  if (busy.value || generating.value) return
  const row = selected.value
  editor.value = { ...row, request_id:crypto.randomUUID(), expected_revision:row.revision,
    source_ids:row.source_refs.map((s:any) => s.document_id), expires:row.valid_until ? row.valid_until.slice(0,10) : '' }
  editing.value = true
}
function bindSources() {
  editor.value.source_refs = editor.value.source_ids.map((id:string) => ({ document_id:id,
    version:sources.value.find(s => s.id === id)?.active_version }))
}
function changed() { editor.value.request_id = crypto.randomUUID() }
async function generate() {
  generating.value = true; error.value = ''; notice.value = ''
  const target = editor.value
  try {
    const request = crypto.randomUUID()
    const conv = await post('/v1/conversations', { request_id:request }, request)
    const message = crypto.randomUUID()
    const run = await post(`/v1/conversations/${conv.id}/messages`, {
      request_id:message, expected_revision:0, mode:'quick_qa', allow_web:false,
      resource_restrictions:editor.value.source_ids,
      text:`请仅根据所选资料起草一页简明工程 Wiki，正文约 500 至 800 字。主题：${editor.value.title}。适用范围：${editor.value.applicability}。使用有条理的 Markdown，保留来源引用和限制，区分合成示例；证据不足请明确说明。` }, message)
    draftRun.value = run.run_id
    let polling = false, ticks = 0
    generationTimer = setInterval(async () => {
      if (polling) return
      polling = true
      try {
        const state = await api(`/v1/runs/${draftRun.value}`)
        if (['succeeded','partial','failed','cancelled'].includes(state.status)) {
          clearInterval(generationTimer); generating.value = false
          if (state.status === 'succeeded') {
            const citations = state.citations || []
            if (!citations.length || citations.some((c:any) => !target.source_refs.some((s:any) => s.document_id === c.document_id && s.version === c.version))) {
              notice.value = '资料版本在起草期间发生变化，或结果缺少有效来源。请重新选择来源后起草。'
              return
            }
            const bibliography = citations.map((c:any) => `[${c.marker}] ${String(c.title).replace(/[\r\n]/g,' ')}；文档 ${c.document_id}；版本 ${c.version}`).join('\n\n')
            target.body_markdown = state.body_markdown + '\n\n## 引用出处\n\n' + bibliography
            changed()
            notice.value = '已生成待审阅草稿，请核对来源与适用范围后保存。生成过程可在工作台查看。'
          } else notice.value = '起草任务未完整完成，可在工作台查看结果；请补充资料或手工编写。'
        } else if (++ticks >= 100) { clearInterval(generationTimer); generating.value = false; notice.value = '起草仍在运行，请到工作台查看进度。' }
      } catch (e) { clearInterval(generationTimer); generating.value = false; error.value = (e as Error).message }
      finally { polling = false }
    }, 3000)
  } catch (e) { generating.value = false; error.value = (e as Error).message }
}
async function save() {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const e = editor.value
    await post('/admin/v1/wiki/drafts', { request_id:e.request_id, document_id:e.document_id,
      expected_revision:e.expected_revision, title:e.title, body_markdown:e.body_markdown, applicability:e.applicability,
      visibility:e.visibility, valid_until:e.expires ? new Date(e.expires + 'T23:59:59+08:00').toISOString() : null,
      source_refs:e.source_refs })
    notice.value = 'Wiki 草稿已保存，索引就绪后请预览并发布。'; editing.value = false; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function publication(item:any, action:'publish'|'unpublish') {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await post(`/admin/v1/knowledge/documents/${item.id}/${action}`, { request_id:crypto.randomUUID(), expected_revision:item.revision,
      ...(action === 'publish' ? { version:item.ingestion.version } : {}) })
    selected.value = null; notice.value = action === 'publish' ? 'Wiki 已发布并进入授权检索范围。' : 'Wiki 已下架，后续检索与读取停止。'; await load()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
onMounted(() => { load(); timer = setInterval(load,5000) }); onUnmounted(() => { clearInterval(timer); clearInterval(generationTimer) })
rememberFilters('WikiPanel', {query})
</script>
<template>
  <section class="resource-panel wiki-panel">
    <div class="page-title"><div><h1>工程 Wiki</h1><p class="muted">把已发布来源整理成工程知识。草稿审核发布后可用于快速问答和智能调查。</p></div><button v-if="manage" class="primary" :disabled="busy || generating" @click="create">编写草稿</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="loading" class="loading-state">正在加载 Wiki…</p><div class="page-toolbar"><input v-model="query" type="search" placeholder="搜索 Wiki 标题" aria-label="搜索 Wiki"/><span class="result-count">{{filtered.length}} 篇文章</span></div>
    <ResourceDrawer v-model="editing" title="编辑 Wiki 草稿" width="960px" :snapshot="editor" :busy="busy || generating" :error="error"><form id="wiki-editor" @input="changed" @submit.prevent="save"><h2>Wiki 草稿</h2><fieldset :disabled="busy || generating">
      <label>标题<input v-model="editor.title" maxlength="180" required /></label>
      <label>适用范围<input v-model="editor.applicability" maxlength="500" placeholder="说明适用对象和边界" required /></label>
      <div class="form-row"><label>可见范围<select v-model="editor.visibility" :disabled="!!editor.document_id"><option value="demo">共享知识</option><option value="private">仅自己</option></select></label><label>有效期至（可留空）<input type="date" v-model="editor.expires" /></label></div>
      <h3 class="section-heading">引用来源 · 已选 {{editor.source_ids.length}} / 12</h3><input v-model="sourceQuery" @input.stop type="search" aria-label="搜索来源文档" placeholder="搜索已发布来源"/><div class="source-picker"><label v-for="source in sources.filter(s=>(editor.visibility==='private' || s.visibility==='demo') && (s.title.toLowerCase().includes(sourceQuery.toLowerCase()) || editor.source_ids.includes(s.id)))" :key="source.id" class="checkbox"><input v-model="editor.source_ids" type="checkbox" :value="source.id" :disabled="editor.source_ids.length>=12 && !editor.source_ids.includes(source.id)" @change="bindSources"/>{{source.title}}</label></div>
      <button type="button" class="secondary" :disabled="!editor.title || !editor.applicability || !editor.source_ids.length || editor.source_ids.length > 12" @click="generate">根据所选来源起草</button>
      <div class="tabs"><button type="button" :aria-selected="!previewMode" @click="previewMode=false">编辑正文</button><button type="button" :aria-selected="previewMode" @click="previewMode=true">预览</button></div><MarkdownAnswer v-if="previewMode" :text="editor.body_markdown || '暂无正文'"/><label v-else>正文（Markdown）<textarea v-model="editor.body_markdown" rows="16" minlength="10" maxlength="100000" required /></label>
      </fieldset><p v-if="generating" role="status">正在根据所选资料起草…</p>
    </form><template #footer><button form="wiki-editor" class="primary" :disabled="busy || generating || !editor.source_ids?.length || !editor.body_markdown?.trim() || editor.source_ids.length>12">保存草稿并建立索引</button></template></ResourceDrawer>
    <div class="document-list"><article v-for="item in filtered" :key="item.id" class="document-row"><div class="document-detail"><strong>{{ item.title }}</strong><p class="muted small">{{ item.available ? '已发布' : item.active_version ? '来源失效或已过期' : '未发布' }}<span v-if="manage && item.ingestion"> · 最新修订：{{ statusNames[item.ingestion.status] || item.ingestion.status }}</span></p></div><div class="row-actions"><button v-if="item.available" class="secondary" :disabled="busy" @click="view(item)">阅读</button><template v-if="manage"><button v-if="item.ingestion?.status === 'staged'" class="secondary" :disabled="busy" @click="view(item,true)">预览草稿</button><button v-if="item.ingestion?.status === 'staged' && selected?.version === item.ingestion.version" class="primary" :disabled="busy" @click="publication(item,'publish')">发布已预览版本</button><button class="secondary" @click="history = item">版本历史</button><button v-if="item.active_version" class="secondary" :disabled="busy" @click="publication(item,'unpublish')">下架</button></template></div></article></div>
    <div v-if="!filtered.length && !error" class="empty-card">{{items.length ? '没有匹配的 Wiki' : '暂无可阅读的 Wiki'}}</div>
    <ResourceDrawer v-model="readable" :title="selected?.title || '阅读 Wiki'" width="1000px"><article v-if="selected"><div class="page-title"><h2>{{ selected.title }}</h2><button v-if="manage" class="secondary" :disabled="busy || generating" @click="edit">修订此页</button></div><p class="muted">适用范围：{{ selected.applicability }} · {{ selected.valid_until ? '有效期至 ' + selected.valid_until : '未设到期日' }}</p><details v-if="headings.length" open><summary>文章目录</summary><div class="wiki-toc"><button v-for="(title,i) in headings" :key="i" class="text-button" @click="jump(i)">{{title}}</button></div></details><div ref="reader"><MarkdownAnswer :text="selected.body_markdown" /></div><details><summary>来源与版本</summary><ul><li v-for="ref in selected.source_refs" :key="ref.document_id">{{ sources.find(s => s.id === ref.document_id)?.title || ref.document_id }} · {{ ref.version }}</li></ul></details></article><template #footer><button v-if="manage && items.find(item=>item.ingestion?.version===selected?.version && item.ingestion?.status==='staged')" class="primary" :disabled="busy" @click="publication(items.find(item=>item.ingestion?.version===selected?.version),'publish')">发布已预览版本</button></template></ResourceDrawer>
    <KnowledgeVersions v-if="history" :document="history" @close="history = null" @changed="load" />
  </section>
</template>
<style scoped>
form label { display:block; margin:12px 0; } input:not([type=checkbox]),textarea,select { width:100%; }.source-picker{max-height:230px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:12px;margin:14px 0}.source-picker label{display:flex;flex-direction:row;gap:8px;align-items:center}.wiki-toc{display:flex;flex-direction:column;align-items:flex-start;padding-bottom:16px} textarea { font:14px/1.7 monospace; resize:vertical; } .wiki-panel :deep(.document-detail) { min-width:0; }
</style>
