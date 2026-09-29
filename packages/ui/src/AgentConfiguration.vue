<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from './api'
const data = ref<any>(null), draft = ref<any>(null), selected = ref<any>(null)
const busy = ref(false), error = ref(''), notice = ref(''), name = ref('配置修订'), reason = ref(''), reviewed = ref(false)
const roleNames: Record<string,string> = { understanding: '意图理解', investigator: '单 Agent / 快速回答', reviewer: '证据审核', supervisor: '多 Agent 调度', sqlbot: '业务查询', rag: '知识检索', tool: '计算与外部资料', rca: '多 Agent 汇总' }
async function load() { data.value = await api('/admin/v1/agent-configuration'); if (!draft.value) draft.value = structuredClone(data.value.active.settings) }
async function act(action: () => Promise<void>) { busy.value = true; error.value = ''; notice.value = ''; try { await action() } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
async function save() { await act(async () => { const result = await post('/admin/v1/agent-configuration/drafts', { request_id: crypto.randomUUID(), expected_revision: data.value.revision, name: name.value, settings: draft.value }); await load(); selected.value = await api('/admin/v1/agent-configuration/versions/' + result.version); reviewed.value = false; notice.value = '草稿已保存，审核发布后用于新任务。' }) }
async function inspect(version: string) { await act(async () => { selected.value = await api('/admin/v1/agent-configuration/versions/' + encodeURIComponent(version)); reviewed.value = false; reason.value = '' }) }
async function publish(action: string) { await act(async () => { await post('/admin/v1/agent-configuration/publish', { request_id: crypto.randomUUID(), expected_revision: data.value.revision, version: selected.value._id || selected.value.version, action, reviewed: reviewed.value, reason: reason.value }); await load(); notice.value = action === 'publish' ? '配置已发布。已有任务保持原版本，新任务使用此版本。' : '已回滚，新任务使用恢复的版本。'; selected.value = null }) }
function useDraft() { draft.value = structuredClone(selected.value.settings); name.value = (selected.value.name || '内置配置') + ' 修订'; selected.value = null }
onMounted(() => act(load))
</script>
<template>
  <section class="resource-panel config-panel"><div class="page-title"><h1>Agent 配置</h1><button class="secondary" :disabled="busy" @click="act(load)">刷新</button></div>
    <p class="muted">模型与角色 Prompt、意图卡一起审核发布。已有任务及其子 Agent 固定使用原版本；发布、回滚只影响新任务。权限、工具范围和自由 Markdown 输出规则由系统保持。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="data && draft">
      <p class="small muted">配置修订 {{ data.revision }} · 当前版本 {{ data.active.version }}</p>
      <form @submit.prevent="save"><fieldset :disabled="busy"><legend>创建草稿</legend>
        <label>修订名称<input v-model="name" maxlength="100" required /></label>
        <label class="check"><input type="checkbox" v-model="draft.multi_agent_enabled" />允许新任务选择多 Agent 协作</label><p v-if="!data.multi_deployment_enabled" class="muted">部署层尚未启用多 Agent，此处设置不能扩大部署能力。</p>
        <details v-for="(label, role) in roleNames" :key="role"><summary>{{ label }} · {{ draft.models[role] }}</summary><label>已批准模型<select v-model="draft.models[role]"><option v-for="model in data.model_choices" :key="model" :value="model">{{ model }}</option></select></label><label>补充表达要求<textarea v-model="draft.role_notes[role]" maxlength="1500" rows="3" placeholder="例如：先解释术语，再列适用边界。" /></label></details>
        <h3>意图卡</h3><p class="small muted">用于理解任务，槽位不是必填条件，不决定单/多 Agent 开关。</p>
        <details v-for="card in draft.intent_cards" :key="card.id"><summary>{{ card.id }} · {{ card.scope }}</summary><label class="check"><input type="checkbox" v-model="card.enabled" />启用此卡</label><label>适用范围<input v-model="card.scope" required maxlength="300" /></label><label>理解规则<textarea v-model="card.rule" required maxlength="1500" rows="3" /></label><label>可识别槽位（中文逗号分隔）<input :value="card.slots.join('，')" @change="card.slots = ($event.target as HTMLInputElement).value.split(/[，,]/).map(s => s.trim()).filter(Boolean)" /></label></details>
        <button class="primary">保存草稿</button>
      </fieldset></form>
      <h3>版本与审核</h3><button class="text-button" :disabled="busy" @click="inspect(data.builtin.version)">查看内置基线</button>
      <div v-for="version in data.versions" :key="version._id" class="version-row"><button class="text-button" :disabled="busy" @click="inspect(version._id)">{{ version.name }}</button><span>{{ version.published_at ? '已发布过' : '草稿' }} · {{ version.created_at }}</span></div>
      <article v-if="selected"><h3>{{ selected.name || '内置基线' }}</h3><p class="small muted">{{ selected._id || selected.version }}<br />{{ selected.review_reason || '尚无审核说明' }}</p><details><summary>查看此版本的模型、补充提示和意图卡</summary><pre>{{ JSON.stringify(selected.settings, null, 2) }}</pre></details><button class="secondary" :disabled="busy" @click="useDraft">以此创建修订</button><label class="check"><input type="checkbox" v-model="reviewed" />已审核此版本的模型、提示词和意图卡</label><label>审核／回滚原因<input v-model="reason" minlength="5" maxlength="300" /></label><div class="row-actions"><button v-if="selected._id && !selected.published_at" class="primary" :disabled="busy || !reviewed || reason.trim().length < 5" @click="publish('publish')">发布此草稿</button><button v-else class="secondary" :disabled="busy || !reviewed || reason.trim().length < 5" @click="publish('rollback')">回滚到此版本</button></div></article>
    </template>
  </section>
</template>
<style scoped>
.config-panel{max-width:1120px}fieldset{border:1px solid var(--border,#dce6df);border-radius:10px;padding:20px}label{display:grid;gap:7px;margin:14px 0}.check{display:flex;gap:8px;align-items:center}input,select,textarea{padding:8px;border:1px solid var(--border,#dce6df);border-radius:6px;color:inherit;background:white}details{margin:15px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:440px;overflow:auto;background:#f5f8f5;padding:16px}.version-row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}article{border:1px solid var(--border,#dce6df);padding:20px;margin-top:20px;border-radius:10px}
</style>
