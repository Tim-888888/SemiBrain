<script setup lang="ts">
import { rememberFilters } from './page-filters'
import { computed, onMounted, ref, watch } from 'vue'
import { api, post } from './api'
import ResourceDrawer from './ResourceDrawer.vue'
import MarkdownAnswer from './MarkdownAnswer.vue'
import UiIcon from './UiIcon.vue'
const editorOpen = ref(false), detailOpen = ref(false), selectedId = ref(''), selectedVersionId = ref(''), search = ref(''), filter = ref('all'), preview = ref(false)
const versions = computed(() => data.value?.versions.filter((v: any) => v.skill_id === selectedId.value) || [])
const selectedItem = computed(() => data.value?.items.find((v: any) => v._id === selectedId.value))
const selectedVersion = computed(() => versions.value.find((v: any) => v._id === selectedVersionId.value))
function latest(item: any) { return data.value?.versions.find((v: any) => v._id === item.active_version) || data.value?.versions.find((v: any) => v.skill_id === item._id) }
const items = computed(() => (data.value?.items || []).filter((item: any) => {
  const v = latest(item)
  return `${item._id} ${v?.definition.name || ''} ${v?.definition.summary || ''}`.toLowerCase().includes(search.value.toLowerCase()) && (filter.value === 'all' || (filter.value === 'enabled' ? item.enabled : !item.enabled))
}))
function inspect(item: any) { selectedId.value = item._id; selectedVersionId.value = latest(item)?._id || ''; reviewed.value = false; reviewNote.value = ''; detailOpen.value = true }
watch(selectedVersionId, () => { reviewed.value = false; reviewNote.value = '' })

const data = ref<any>(null), busy = ref(false), error = ref(''), notice = ref('')
const skillId = ref(''), revision = ref(0), reviewed = ref(false), reviewNote = ref('')
const roles = [['single_agent', '单 Agent'], ['tool', '工具 Agent'], ['rag', '知识 Agent'], ['sqlbot', '数据 Agent'], ['vision', '视觉 Agent']]
function empty() { return { name: '', summary: '', instructions: '', user_roles: ['admin', 'user'], agent_roles: ['single_agent', 'tool'], required_tools: [] as string[], script: '', exports: [] as string[], seconds: 20 } }
const definition = ref(empty()), schema = ref('{"type":"object","properties":{},"additionalProperties":false}'), dependencies = ref(''), exportsText = ref('')
async function load() { data.value = await api('/admin/v1/skills'); const row = data.value.items.find((item: any) => item._id === skillId.value); revision.value = row?.revision || 0 }
async function action(work: () => Promise<void>) { busy.value = true; error.value = ''; notice.value = ''; try { await work() } catch (e: any) { error.value = e.message } finally { busy.value = false } }
function select(item?: any, version?: any) {
  skillId.value = item?._id || ''; revision.value = item?.revision || 0
  definition.value = version ? JSON.parse(JSON.stringify(version.definition)) : empty()
  schema.value = JSON.stringify(version?.definition.parameter_schema || { type: 'object', properties: {}, additionalProperties: false }, null, 2)
  dependencies.value = definition.value.required_tools.join(', '); exportsText.value = definition.value.exports.join('\n')
  reviewed.value = false; reviewNote.value = ''; detailOpen.value = false; preview.value = false; editorOpen.value = true
}
async function save() { await action(async () => {
  let parameter_schema: any; try { parameter_schema = JSON.parse(schema.value) } catch { throw new Error('参数定义不是有效 JSON。') }
  await post('/admin/v1/skills', { request_id: crypto.randomUUID(), skill_id: skillId.value, expected_revision: revision.value,
    definition: { ...definition.value, parameter_schema, required_tools: dependencies.value.split(',').map(x => x.trim()).filter(Boolean), exports: exportsText.value.split('\n').map(x => x.trim()).filter(Boolean) } })
  await load(); editorOpen.value = false; notice.value = '新版本草稿已保存，审核发布后供新运行使用。'
}) }
async function change(item: any, operation: string, version?: any) { await action(async () => {
  await post('/admin/v1/skills/' + item._id, { request_id: crypto.randomUUID(), expected_revision: item.revision, action: operation,
    version_id: version?._id || null, reviewed: reviewed.value, review_note: reviewNote.value })
  await load(); reviewed.value = false; reviewNote.value = ''; notice.value = operation === 'disable' ? '技能已停用，执行和关联产物会重新校验。' : '已更新。正在执行的任务保持原版本。'
}) }
onMounted(() => action(load))
rememberFilters('SkillsPanel', {search,filter})
</script>

<template>
  <section class="skills-panel">
    <div class="page-title"><div><h1>Skills 技能</h1><p class="muted">将经过审核的方法与脚本，交给合适的 Agent。</p></div><button class="primary" :disabled="busy" @click="select()">＋ 新建技能</button></div>
    <p v-if="error" class="error" role="alert">{{ error }} <button v-if="!data" class="text-button" @click="action(load)">重试</button></p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <div class="page-toolbar"><input v-model="search" type="search" placeholder="搜索技能名称、标识或用途" aria-label="搜索技能" /><select v-model="filter" aria-label="技能状态"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">未启用</option></select><span class="result-count">{{ items.length }} 个技能</span></div>
    <div v-if="busy && !data" class="loading-state" role="status">正在加载技能目录…</div>
    <div v-else-if="!items.length && !error" class="empty-card"><strong>{{ data?.items.length ? '没有匹配的技能' : '创建第一个技能' }}</strong><p>{{ data?.items.length ? '试试其他关键词或状态。' : '保存方法说明，审核后发布给 Agent 使用。' }}</p></div>
    <div class="resource-grid"><article v-for="item in items" :key="item._id" class="resource-card">
      <div class="card-heading"><span class="resource-icon"><UiIcon name="skills" /></span><div><h3>{{ latest(item)?.definition.name || item._id }}</h3><span class="small muted">{{ item._id }}</span></div></div>
      <p class="card-description">{{ latest(item)?.definition.summary || '尚无用途说明' }}</p>
      <div class="card-meta"><span class="status-pill" :class="{neutral: !item.enabled}">{{ item.enabled ? '已启用' : '未启用' }}</span><span>{{ item.active_version ? '已有发布版本' : '尚未发布' }}</span><span>修订 {{ item.revision }}</span></div>
      <div class="card-actions"><button class="secondary" :disabled="busy" @click="inspect(item)">详情与版本</button><button class="text-button" :disabled="busy" @click="select(item,latest(item))">编辑</button></div>
    </article></div>
    <ResourceDrawer v-model="editorOpen" :title="revision ? '修订技能 · ' + skillId : '新建技能'" :busy="busy" :snapshot="{skillId,definition,schema,dependencies,exportsText}" :error="error">
      <form id="skill-editor" @submit.prevent="save"><fieldset :disabled="busy">
        <h3 class="section-heading">基本信息</h3><div class="fields"><label>技能标识<input v-model="skillId" required pattern="[a-z][a-z0-9_\-]*" :readonly="revision > 0" placeholder="例如 report-summary" /></label><label>名称<input v-model="definition.name" required /></label></div>
        <label>用途摘要<textarea v-model="definition.summary" required rows="2" maxlength="500" /></label>
        <div class="tabs"><button type="button" :aria-selected="!preview" @click="preview=false">编写操作说明</button><button type="button" :aria-selected="preview" @click="preview=true">预览</button></div>
        <MarkdownAnswer v-if="preview" :text="definition.instructions || '暂无说明内容。'" /><label v-else>操作说明（Markdown）<textarea v-model="definition.instructions" required rows="10" /></label>
        <h3 class="section-heading">使用范围</h3><div class="choices"><span>使用者</span><label><input v-model="definition.user_roles" type="checkbox" value="admin" />管理员</label><label><input v-model="definition.user_roles" type="checkbox" value="user" />普通用户</label></div>
        <div class="choices"><span>执行角色</span><label v-for="role in roles" :key="role[0]"><input v-model="definition.agent_roles" :value="role[0]" type="checkbox" />{{ role[1] }}</label></div>
        <label>依赖工具（英文逗号分隔）<input v-model="dependencies" placeholder="例如 knowledge.search, knowledge.read；无依赖可留空" /></label>
        <details><summary>可选脚本与高级参数</summary><p class="muted small">脚本在无网络的 Docker 中执行；仅登记的输出文件会成为产物，最长 30 秒。</p><label>参数定义（JSON Schema）<textarea v-model="schema" rows="5" spellcheck="false" /></label><label>Python 代码<textarea v-model="definition.script" rows="10" spellcheck="false" /></label><div class="fields"><label>输出文件名（每行一个）<textarea v-model="exportsText" rows="3" placeholder="分析结果.md" /></label><label>最长执行秒数<input v-model.number="definition.seconds" type="number" min="1" max="30" required /></label></div></details>
      </fieldset></form>
      <template #footer><button type="submit" class="primary" form="skill-editor" :disabled="busy">{{ busy ? '正在保存…' : '保存新草稿版本' }}</button></template>
    </ResourceDrawer>
    <ResourceDrawer v-model="detailOpen" :title="selectedVersion?.definition.name || '技能详情'" :busy="busy" :error="error" :snapshot="{reviewed,reviewNote}">
      <template v-if="selectedItem"><div class="row-actions"><span class="status-pill">{{ selectedItem.enabled ? '已启用' : '未启用' }}</span><button v-if="selectedItem.active_version" class="secondary" :disabled="busy" @click="change(selectedItem,selectedItem.enabled ? 'disable':'enable')">{{ selectedItem.enabled ? '停用技能' : '启用已发布版本' }}</button></div>
        <label>查看版本<select v-model="selectedVersionId" :disabled="busy"><option v-for="v in versions" :key="v._id" :value="v._id">{{ v.status === 'draft' ? '待审核草稿' : '已发布' }}{{ selectedItem.active_version === v._id ? ' · 当前生效' : '' }} · {{ new Date(v.created_at).toLocaleString() }}</option></select></label>
        <template v-if="selectedVersion"><p class="muted">{{ selectedVersion.definition.summary }}</p><MarkdownAnswer :text="selectedVersion.definition.instructions" />
          <h3 class="section-heading">权限与产物</h3><p>账号：{{ selectedVersion.definition.user_roles.join('、') }}</p><p>Agent：{{ selectedVersion.definition.agent_roles.map((id: string) => roles.find(r => r[0] === id)?.[1] || id).join('、') }}</p><p>依赖工具：{{ selectedVersion.definition.required_tools.join('、') || '无' }}</p><p>输出文件：{{ selectedVersion.definition.exports.join('、') || '无' }}</p>
          <details v-if="selectedVersion.definition.script"><summary>查看脚本与参数</summary><pre>{{ selectedVersion.definition.script }}</pre><pre>{{ JSON.stringify(selectedVersion.definition.parameter_schema,null,2) }}</pre></details>
          <details><summary>版本信息与审核记录</summary><p class="small">{{ selectedVersion._id }}</p><p>{{ selectedVersion.review_note || '尚无审核记录' }}</p></details>
          <template v-if="selectedVersion.status === 'draft'"><h3 class="section-heading">审核当前所选版本</h3><label>审核说明<input v-model="reviewNote" :disabled="busy" placeholder="记录检查的方法、权限、脚本与输出范围" /></label><label class="checkbox"><input v-model="reviewed" type="checkbox" :disabled="busy" />我已核对当前所选版本</label></template>
        </template>
      </template>
      <template #footer><button class="secondary" :disabled="busy || !selectedVersion" @click="select(selectedItem,selectedVersion)">以此创建修订</button><button v-if="selectedVersion?.status === 'draft'" class="primary" :disabled="busy || !reviewed || !reviewNote.trim()" @click="change(selectedItem,'publish',selectedVersion)">发布所选版本</button></template>
    </ResourceDrawer>
  </section>
</template>
