<script setup lang="ts">
import RefreshDataButton from './RefreshDataButton.vue'
import { rememberFilters } from './page-filters'
import { computed, onMounted, ref } from 'vue'
import { api, post } from './api'
import ResourceDrawer from './ResourceDrawer.vue'
const editing = ref(false), query = ref(''), kind = ref('')
const filtered = computed(() => rows.value.filter(row => (!kind.value || row.kind===kind.value) && row.content.toLowerCase().includes(query.value.toLowerCase())))
function create() { candidate.value=null; draft.value=fresh(); confirmed.value=false; editing.value=true }

const busy = ref(false), error = ref(''), note = ref('')
const rows = ref<any[]>([]), enabled = ref(false), revision = ref(0)
const candidates = ref<any[]>([]), candidate = ref<any>(null), autoExtract = ref(false)
const draft = ref(fresh()), confirmed = ref(false)
function fresh() { return { memory_id: crypto.randomUUID(), expected_revision: 0, kind: 'preference', content: '', source_run_id: '', expires_at: new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10) } }
const names: Record<string, string> = { preference: '回答偏好', background: '工作背景', investigation_summary: '调查摘要' }
const states: Record<string, string> = { active: '有效', expired: '已过期', source_unavailable: '来源不可用' }
const sources: Record<string,string> = {user_confirmed:'本人确认',confirmed_chat_candidate:'聊天提取 · 本人确认',confirmed_report_excerpt:'已确认原回答摘录'}
async function load() { const data = await api('/v1/memories'); rows.value = data.items; enabled.value = data.enabled; revision.value = data.revision; candidates.value = data.candidates || []; autoExtract.value = !!data.auto_extract }
async function perform(action: () => Promise<void>) { busy.value = true; error.value = ''; note.value = ''; try { await action(); await load() } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
function edit(row: any) { candidate.value=null; draft.value = { memory_id: row._id, expected_revision: row.revision, kind: row.kind, content: row.content, source_run_id: row.source_run_id || '', expires_at: row.expires_at.slice(0, 10) }; confirmed.value = false; editing.value = true }
function review(row: any) { candidate.value=row; draft.value={...fresh(),kind:row.kind,content:row.content}; confirmed.value=false; editing.value=true }
async function save() { await perform(async () => {
  const common={kind:draft.value.kind,content:draft.value.content,expires_at:new Date(draft.value.expires_at+'T23:59:59').toISOString(),confirmed:confirmed.value,request_id:crypto.randomUUID()}
  if(candidate.value) await post(`/v1/memories/candidates/${candidate.value._id}/confirm`,common)
  else await post('/v1/memories',{...draft.value,...common,source_run_id:draft.value.kind==='investigation_summary'?draft.value.source_run_id:null})
  draft.value=fresh(); candidate.value=null; confirmed.value=false; editing.value=false; note.value='记忆已保存。'
}) }
async function toggle() { await perform(async () => { await post('/v1/memories/settings', { request_id: crypto.randomUUID(), expected_revision: revision.value, enabled: !enabled.value }); note.value = enabled.value ? '已关闭记忆。' : '已开启记忆。' }) }
async function toggleAutomatic() { await perform(async () => { await post('/v1/memories/settings',{request_id:crypto.randomUUID(),expected_revision:revision.value,enabled:enabled.value,auto_extract:!autoExtract.value}); note.value=autoExtract.value?'已关闭自动提取。':'已开启自动提取，新对话内容会在后台整理为待确认候选。' }) }
async function dismiss(row:any) { await perform(async()=>{await post(`/v1/memories/candidates/${row._id}/dismiss`,{request_id:crypto.randomUUID()});note.value='已忽略此建议。'}) }
const removing = ref('')
async function remove(row: any) { await perform(async () => { await post('/v1/memories/' + row._id + '/delete', { request_id: crypto.randomUUID(), expected_revision: row.revision }); removing.value = ''; if (draft.value.memory_id === row._id) draft.value = fresh(); note.value = '记忆已删除，不再用于新回答。' }) }
onMounted(() => perform(async () => {}))
rememberFilters('MemoryPanel', {query,kind})
</script>
<template>
  <section class="memory-panel">
    <div class="page-title"><h1>我的记忆</h1><div class="row-actions"><RefreshDataButton :refresh="()=>perform(async()=>{})" :disabled="busy || editing"/><button class="primary" :disabled="busy" @click="create">＋ 添加记忆</button><button class="secondary" :disabled="busy" @click="toggle">{{ enabled ? '关闭记忆' : '开启记忆' }}</button></div></div>
    <p class="muted">{{ enabled ? '已开启' : '未开启' }} · 仅自己可用。你确认保存的偏好与背景可以跨会话使用；本轮要求优先。调查摘要只帮助定位来源，不代替最新证据。</p>
    <p class="small muted">修改、关闭、删除或过期会停止仍在使用旧记忆的任务，重新提交即可使用最新内容。删除记忆不会删除原会话及已生成的历史回答。</p>
    <div class="memory-automatic"><label class="memory-confirm"><input type="checkbox" :checked="autoExtract" :disabled="busy || !enabled || editing" @change="toggleAutomatic" />自动提取记忆候选</label><p class="small muted">只提取你的长期回答偏好和工作背景，确认后才会用于回答。关闭记忆会同时停止提取和调用，已保存内容仍保留。</p></div>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="note" role="status">{{ note }}</p>
    <section v-if="candidates.length" class="memory-candidates" aria-label="待确认记忆">
      <div class="memory-section-heading"><h2>待确认</h2><span class="memory-count">{{candidates.length}} 条</span></div>
      <p class="memory-section-description">以下建议尚未用于回答，可以修改后确认，也可以忽略。</p>
      <div class="memory-card-list"><article v-for="row in candidates" :key="row._id" class="panel-card memory-card">
        <header class="memory-card-heading"><strong>{{names[row.kind]}}</strong><span class="status-pill neutral">待确认</span></header>
        <p class="memory-content">{{row.content}}</p>
        <blockquote v-if="row.quote" class="memory-quote"><span class="memory-quote-label">你的原话</span>{{row.quote}}</blockquote>
        <p v-if="row.replaces_content" class="memory-meta">确认后替换：{{row.replaces_content}}</p>
        <footer class="memory-card-actions"><button class="primary" :disabled="busy || !enabled || row.state!=='active'" @click="review(row)">查看并确认</button><button class="secondary" :disabled="busy" @click="dismiss(row)">忽略</button></footer>
      </article></div>
    </section>
    <div class="memory-section-heading memory-saved-heading"><h2>已保存</h2></div>
    <div class="page-toolbar"><input v-model="query" type="search" placeholder="搜索记忆内容" aria-label="搜索记忆"/><select v-model="kind" aria-label="记忆类型"><option value="">全部类型</option><option v-for="(label,key) in names" :value="key">{{label}}</option></select><span class="result-count">{{filtered.length}} 条记忆</span></div>
    <ResourceDrawer v-model="editing" :title="candidate ? '确认记忆建议' : draft.expected_revision ? '纠正记忆' : '添加记忆'" :snapshot="{draft,confirmed}" :busy="busy" :error="error"><form id="memory-editor" @submit.prevent="save"><fieldset :disabled="busy"><legend>{{ candidate ? '确认记忆建议' : draft.expected_revision ? '纠正记忆' : '添加记忆' }}</legend>
      <blockquote v-if="candidate" class="memory-quote">你的原话：{{candidate.quote}}</blockquote><p v-if="candidate?.replaces_content" class="small muted">确认后替换原记忆：{{candidate.replaces_content}}</p>
      <label>类型<select v-model="draft.kind"><template v-for="(label,key) in names" :key="key"><option v-if="!candidate || key!=='investigation_summary'" :value="key">{{ label }}</option></template></select></label>
      <label v-if="draft.kind === 'investigation_summary'">原回答运行编号<input v-model="draft.source_run_id" required placeholder="从原回答的运行详情复制 run_id" /></label>
      <label>{{ draft.kind === 'investigation_summary' ? '已确认摘要（从原回答复制连续原文）' : '内容' }}<textarea v-model="draft.content" required maxlength="1600" rows="5" /></label>
      <label>有效至<input v-model="draft.expires_at" type="date" required /></label>
      <label class="memory-confirm"><input v-model="confirmed" type="checkbox" required />我已确认内容；调查中的未验证猜测不能作为长期事实</label>

    </fieldset></form><template #footer><button form="memory-editor" class="primary" :disabled="busy || !confirmed || (!!candidate && !enabled)">确认保存</button></template></ResourceDrawer>
    <div v-if="!filtered.length && !busy && !error" class="empty-card"><strong>{{rows.length ? '没有匹配的记忆' : '还没有保存记忆'}}</strong><p>确认保存的偏好与背景，可以用于后续回答。</p></div>
    <div class="memory-card-list"><article v-for="row in filtered" class="panel-card memory-card" :key="row._id">
      <header class="memory-card-heading"><strong>{{ names[row.kind] }}</strong><span class="status-pill" :class="{neutral:row.state!=='active'}">{{ states[row.state] }}</span></header>
      <p class="memory-content">{{ row.content }}</p>
      <div class="memory-meta"><span>{{ sources[row.source_kind] || '本人确认' }}</span><span>修订 {{ row.revision }}</span><span>有效至 {{ new Date(row.expires_at).toLocaleDateString() }}</span></div>
      <p v-if="row.source_run_id" class="memory-meta">原回答：{{ row.source_run_id }}</p>
      <footer class="memory-card-actions"><button class="secondary" :disabled="busy || row.state === 'source_unavailable'" @click="edit(row)">纠正</button><button v-if="removing !== row._id" class="text-button" :disabled="busy" @click="removing = row._id">删除</button><template v-else><span class="memory-delete-notice">删除后无法恢复。</span><button class="secondary" :disabled="busy" @click="remove(row)">确认删除</button><button class="text-button" @click="removing = ''">取消</button></template></footer>
    </article></div>
  </section>
</template>
<style scoped>
.memory-panel{overflow:auto;padding:28px;max-width:1050px;width:100%;margin:0 auto}
fieldset{border:1px solid var(--border,#dce6df);border-radius:10px;padding:20px}
label{display:grid;gap:7px;margin-bottom:15px}
input,select,textarea{max-width:100%;padding:9px;border:1px solid var(--border,#dce6df);border-radius:6px;color:inherit;background:white}
.memory-confirm{display:flex;flex-direction:row;align-items:center;gap:8px}
.memory-confirm input{width:16px;height:16px;flex:0 0 auto;margin:0;padding:0;accent-color:var(--accent)}
.row-actions{flex-wrap:wrap}
.memory-automatic{padding:16px 18px;border:1px solid var(--border,#dce6df);border-radius:10px;margin:20px 0}
.memory-automatic label{margin:0;font-weight:600}.memory-automatic p{margin:8px 0 0;line-height:1.7}
.memory-candidates{margin:28px 0 32px}
.memory-section-heading{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.memory-section-heading h2{margin:0;font-size:18px;font-weight:600}
.memory-count{font-size:12px;color:var(--muted);background:#eaf0f3;border-radius:6px;padding:2px 8px}
.memory-section-description{margin:0 0 16px;color:var(--muted);font-size:13px;line-height:1.7}
.memory-saved-heading{margin:28px 0 16px}
.memory-card-list{display:grid;gap:16px;min-width:0}
.memory-panel .memory-card{display:flex;flex-direction:column;gap:14px;min-width:0;margin:0;padding:22px 24px;border:1px solid var(--border);border-radius:12px;background:var(--surface,#fff)}
.memory-card-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
.memory-card-heading strong{font-size:15px;font-weight:600;line-height:1.5}
.memory-content{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px;line-height:1.8;color:var(--ink)}
.memory-quote{margin:0;padding:12px 16px;border-left:3px solid #c4dbd4;border-radius:0 8px 8px 0;background:#f6f9f8;color:var(--muted,#64748b);font-size:13px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere}
.memory-quote-label{display:block;margin-bottom:4px;color:#536b61;font-size:12px;font-weight:600}
.memory-meta{display:flex;flex-wrap:wrap;gap:6px 16px;margin:0;color:var(--muted);font-size:12px;line-height:1.7;overflow-wrap:anywhere}
.memory-card-actions{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:10px;margin-top:2px;padding-top:16px;border-top:1px solid #edf1f4}
.memory-card-actions button{min-height:38px;padding:8px 16px;font-size:13px;border-radius:8px}
.memory-delete-notice{margin-right:auto;font-size:12px;color:var(--muted)}
@media(max-width:650px){
  .memory-panel{padding:20px 16px}.memory-panel .page-title{flex-direction:column;align-items:flex-start;gap:14px;margin-bottom:20px}
  .memory-panel .page-title .row-actions{width:100%;display:grid;grid-template-columns:auto 1fr 1fr;gap:8px}
  .memory-panel .page-title button{min-height:40px;padding:8px 10px;font-size:12px}
  .memory-panel fieldset{padding:12px}.memory-automatic{padding:14px}
  .memory-panel .memory-card{padding:18px 16px;gap:12px}.memory-card-heading strong,.memory-content{font-size:14px}
  .memory-quote{padding:10px 12px;font-size:13px}.memory-card-actions{gap:8px;padding-top:14px}
  .memory-card-actions button{flex:1;min-height:42px;padding:9px 12px}.memory-delete-notice{flex-basis:100%}
  .memory-panel .page-toolbar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;margin-bottom:16px}
  .memory-panel .page-toolbar input{grid-column:1/-1;width:100%;min-width:0}
  .memory-panel .page-toolbar select{width:100%;min-width:0}.memory-panel .page-toolbar .result-count{white-space:nowrap}
}
</style>
