<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from './api'
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
  reviewed.value = false; reviewNote.value = ''
}
async function save() { await action(async () => {
  let parameter_schema: any; try { parameter_schema = JSON.parse(schema.value) } catch { throw new Error('参数定义不是有效 JSON。') }
  await post('/admin/v1/skills', { request_id: crypto.randomUUID(), skill_id: skillId.value, expected_revision: revision.value,
    definition: { ...definition.value, parameter_schema, required_tools: dependencies.value.split(',').map(x => x.trim()).filter(Boolean), exports: exportsText.value.split('\n').map(x => x.trim()).filter(Boolean) } })
  await load(); notice.value = '新版本草稿已保存，审核发布后供新运行使用。'
}) }
async function change(item: any, operation: string, version?: any) { await action(async () => {
  await post('/admin/v1/skills/' + item._id, { request_id: crypto.randomUUID(), expected_revision: item.revision, action: operation,
    version_id: version?._id || null, reviewed: reviewed.value, review_note: reviewNote.value })
  await load(); reviewed.value = false; reviewNote.value = ''; notice.value = operation === 'disable' ? '技能已停用，执行和关联产物会重新校验。' : '已更新。正在执行的任务保持原版本。'
}) }
onMounted(() => action(load))
</script>

<template>
  <section class="skills-panel"><h2>Skills 技能</h2><p>将经过审核的方法说明和 Python 脚本发布给 Agent。上传知识文档不会自动成为技能；正文回答仍使用自由 Markdown。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <button :disabled="busy" @click="select()">新建技能</button>
    <fieldset :disabled="busy"><legend>{{ revision ? '创建修订草稿' : '新建草稿' }}</legend>
      <div class="fields"><label>技能标识<input v-model="skillId" :readonly="revision > 0" placeholder="英文小写，例如 report-summary" /></label><label>名称<input v-model="definition.name" /></label></div>
      <label>用途摘要<textarea v-model="definition.summary" rows="2" maxlength="500" /></label><label>操作说明（Markdown）<textarea v-model="definition.instructions" rows="6" /></label>
      <div class="choices"><span>使用者</span><label><input v-model="definition.user_roles" type="checkbox" value="admin" />管理员</label><label><input v-model="definition.user_roles" type="checkbox" value="user" />普通用户</label></div>
      <div class="choices"><span>执行角色</span><label v-for="role in roles" :key="role[0]"><input v-model="definition.agent_roles" :value="role[0]" type="checkbox" />{{ role[1] }}</label></div>
      <label>依赖工具（英文逗号分隔）<input v-model="dependencies" placeholder="例如 knowledge.search, knowledge.read；无依赖可留空" /></label>
      <details><summary>可选：审核脚本</summary><p>Python 脚本在无网络的 Docker 中执行，参数由 parameters 字典读取。依赖库须已包含在沙箱镜像内；最多 30 秒，仅登记的输出文件会成为产物。</p>
        <label>参数定义（JSON Schema）<textarea v-model="schema" rows="5" spellcheck="false" /></label><label>Python 代码<textarea v-model="definition.script" rows="10" spellcheck="false" /></label>
        <div class="fields"><label>输出文件名（每行一个）<textarea v-model="exportsText" rows="3" placeholder="分析结果.md" /></label><label>最长执行秒数<input v-model.number="definition.seconds" type="number" min="1" max="30" /></label></div>
      </details><button @click="save">保存新草稿版本</button>
    </fieldset>
    <h3>审核与已发布版本</h3><label>审核说明<input v-model="reviewNote" :disabled="busy" placeholder="记录已检查的方法、脚本、依赖与输出范围" /></label><label class="review"><input v-model="reviewed" type="checkbox" :disabled="busy" />我已检查本次发布版本的说明、权限、脚本和产物范围</label>
    <p v-if="data && !data.items.length">尚未创建技能。</p>
    <article v-for="item in data?.items || []" :key="item._id"><div class="heading"><strong>{{ item._id }}</strong><span>{{ item.enabled ? '已启用' : '未启用' }} · 修订 {{ item.revision }}</span><button v-if="item.active_version" :disabled="busy" @click="change(item, item.enabled ? 'disable' : 'enable')">{{ item.enabled ? '停用' : '启用已发布版本' }}</button></div>
      <details v-for="version in data.versions.filter((v: any) => v.skill_id === item._id)" :key="version._id"><summary>{{ version.definition.name }} · {{ version.status === 'draft' ? '待审核' : '已发布' }}{{ item.active_version === version._id ? ' · 当前版本' : '' }} · {{ new Date(version.created_at).toLocaleString() }}</summary>
        <p>{{ version.definition.summary }}</p><pre>{{ version.definition.instructions }}</pre><details v-if="version.definition.script"><summary>脚本、参数与输出</summary><pre>{{ version.definition.script }}</pre><pre>{{ JSON.stringify(version.definition.parameter_schema, null, 2) }}</pre><p>{{ version.definition.exports.join('、') }}</p></details>
        <small>版本 {{ version._id }} · {{ version.review_note || '尚无审核记录' }}</small><div class="heading"><button :disabled="busy" @click="select(item, version)">以此创建修订</button><button v-if="version.status === 'draft'" :disabled="busy || !reviewed || !reviewNote.trim()" @click="change(item, 'publish', version)">发布此版本</button></div>
      </details>
    </article>
  </section>
</template>

<style scoped>
.skills-panel{padding:1.8rem;overflow:auto;flex:1}.fields,.choices,.heading{display:flex;flex-wrap:wrap;gap:1rem;align-items:center;margin:.8rem 0}.fields>label{flex:1;min-width:14rem}fieldset{border:1px solid #dce5dc;padding:1rem;margin:1rem 0;border-radius:.7rem}label{display:block;margin:.7rem 0}label>textarea,label>input:not([type=checkbox]){display:block;width:100%;margin-top:.3rem}textarea{resize:vertical;padding:.6rem}.choices label,.review{display:flex;gap:.35rem;align-items:center}article{border:1px solid #dce5dc;border-radius:.7rem;padding:1rem;margin:1rem 0}details{margin:.7rem 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow:auto;max-height:25rem}small{color:#677b72}
</style>
