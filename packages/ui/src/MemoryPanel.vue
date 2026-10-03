<script setup lang="ts">
import { rememberFilters } from './page-filters'
import { computed, onMounted, ref } from 'vue'
import { api, post } from './api'
import ResourceDrawer from './ResourceDrawer.vue'
const editing = ref(false), query = ref(''), kind = ref('')
const filtered = computed(() => rows.value.filter(row => (!kind.value || row.kind===kind.value) && row.content.toLowerCase().includes(query.value.toLowerCase())))
function create() { draft.value=fresh(); confirmed.value=false; editing.value=true }

const busy = ref(false), error = ref(''), note = ref('')
const rows = ref<any[]>([]), enabled = ref(false), revision = ref(0)
const draft = ref(fresh()), confirmed = ref(false)
function fresh() { return { memory_id: crypto.randomUUID(), expected_revision: 0, kind: 'preference', content: '', source_run_id: '', expires_at: new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10) } }
const names: Record<string, string> = { preference: '回答偏好', background: '工作背景', investigation_summary: '调查摘要' }
const states: Record<string, string> = { active: '有效', expired: '已过期', source_unavailable: '来源不可用' }
async function load() { const data = await api('/v1/memories'); rows.value = data.items; enabled.value = data.enabled; revision.value = data.revision }
async function perform(action: () => Promise<void>) { busy.value = true; error.value = ''; note.value = ''; try { await action(); await load() } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
function edit(row: any) { draft.value = { memory_id: row._id, expected_revision: row.revision, kind: row.kind, content: row.content, source_run_id: row.source_run_id || '', expires_at: row.expires_at.slice(0, 10) }; confirmed.value = false; editing.value = true }
async function save() { await perform(async () => { await post('/v1/memories', { ...draft.value, source_run_id: draft.value.kind === 'investigation_summary' ? draft.value.source_run_id : null, expires_at: new Date(draft.value.expires_at + 'T23:59:59').toISOString(), confirmed: confirmed.value, request_id: crypto.randomUUID() }); draft.value = fresh(); confirmed.value = false; editing.value = false; note.value = '记忆已保存。' }) }
async function toggle() { await perform(async () => { await post('/v1/memories/settings', { request_id: crypto.randomUUID(), expected_revision: revision.value, enabled: !enabled.value }); note.value = enabled.value ? '已关闭记忆。' : '已开启记忆。' }) }
const removing = ref('')
async function remove(row: any) { await perform(async () => { await post('/v1/memories/' + row._id + '/delete', { request_id: crypto.randomUUID(), expected_revision: row.revision }); removing.value = ''; if (draft.value.memory_id === row._id) draft.value = fresh(); note.value = '记忆已删除，不再用于新回答。' }) }
onMounted(() => perform(async () => {}))
rememberFilters('MemoryPanel', {query,kind})
</script>
<template>
  <section class="memory-panel">
    <div class="page-title"><h1>我的记忆</h1><div class="row-actions"><button class="primary" :disabled="busy" @click="create">＋ 添加记忆</button><button class="secondary" :disabled="busy" @click="toggle">{{ enabled ? '关闭记忆' : '开启记忆' }}</button></div></div>
    <p class="muted">{{ enabled ? '已开启' : '未开启' }} · 仅自己可用。你确认保存的偏好与背景可以跨会话使用；本轮要求优先。调查摘要只帮助定位来源，不代替最新证据。</p>
    <p class="small muted">修改、关闭、删除或过期会停止仍在使用旧记忆的任务，重新提交即可使用最新内容。删除记忆不会删除原会话及已生成的历史回答。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="note" role="status">{{ note }}</p>
    <div class="page-toolbar"><input v-model="query" type="search" placeholder="搜索记忆内容" aria-label="搜索记忆"/><select v-model="kind" aria-label="记忆类型"><option value="">全部类型</option><option v-for="(label,key) in names" :value="key">{{label}}</option></select><span class="result-count">{{filtered.length}} 条记忆</span></div>
    <ResourceDrawer v-model="editing" :title="draft.expected_revision ? '纠正记忆' : '添加记忆'" :snapshot="{draft,confirmed}" :busy="busy" :error="error"><form id="memory-editor" @submit.prevent="save"><fieldset :disabled="busy"><legend>{{ draft.expected_revision ? '纠正记忆' : '添加记忆' }}</legend>
      <label>类型<select v-model="draft.kind"><option v-for="(label, key) in names" :value="key" :key="key">{{ label }}</option></select></label>
      <label v-if="draft.kind === 'investigation_summary'">原回答运行编号<input v-model="draft.source_run_id" required placeholder="从原回答的运行详情复制 run_id" /></label>
      <label>{{ draft.kind === 'investigation_summary' ? '已确认摘要（从原回答复制连续原文）' : '内容' }}<textarea v-model="draft.content" required maxlength="1600" rows="5" /></label>
      <label>有效至<input v-model="draft.expires_at" type="date" required /></label>
      <label class="memory-confirm"><input v-model="confirmed" type="checkbox" required />我已确认内容；调查中的未验证猜测不能作为长期事实</label>

    </fieldset></form><template #footer><button form="memory-editor" class="primary" :disabled="busy || !confirmed">确认保存</button></template></ResourceDrawer>
    <div v-if="!filtered.length && !busy && !error" class="empty-card"><strong>{{rows.length ? '没有匹配的记忆' : '还没有保存记忆'}}</strong><p>确认保存的偏好与背景，可以用于后续回答。</p></div>
    <article v-for="row in filtered" class="panel-card" :key="row._id"><strong>{{ names[row.kind] }} · {{ states[row.state] }}</strong><p class="memory-content">{{ row.content }}</p><p class="small muted">修订 {{ row.revision }} · {{ row.source_kind === 'user_confirmed' ? '本人确认' : '已确认原回答摘录' }} · 有效至 {{ new Date(row.expires_at).toLocaleDateString() }}</p><p v-if="row.source_run_id" class="small muted">原回答：{{ row.source_run_id }}</p><div class="row-actions"><button class="secondary" :disabled="busy || row.state === 'source_unavailable'" @click="edit(row)">纠正</button><button v-if="removing !== row._id" class="text-button" :disabled="busy" @click="removing = row._id">删除</button><template v-else><span>删除后无法恢复。</span><button class="secondary" :disabled="busy" @click="remove(row)">确认删除</button><button class="text-button" @click="removing = ''">取消</button></template></div></article>
  </section>
</template>
<style scoped>
.memory-panel{overflow:auto;padding:28px;max-width:1050px;width:100%;margin:0 auto}fieldset{border:1px solid var(--border,#dce6df);border-radius:10px;padding:20px}label{display:grid;gap:7px;margin-bottom:15px}input,select,textarea{max-width:100%;padding:9px;border:1px solid var(--border,#dce6df);border-radius:6px;color:inherit;background:white}article{border-bottom:1px solid var(--border,#dce6df);padding:22px 0}.memory-content{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8}.memory-confirm{display:flex;align-items:center;gap:8px}.row-actions{flex-wrap:wrap}
</style>
