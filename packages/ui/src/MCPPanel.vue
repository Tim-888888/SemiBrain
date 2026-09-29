<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from './api'
const data = ref<any>(null), busy = ref(false), error = ref(''), notice = ref('')
const roles = [['single_agent', '单 Agent'], ['tool', '工具 Agent'], ['rag', '知识 Agent'], ['sqlbot', '数据 Agent'], ['vision', '视觉 Agent']]
async function load() { data.value = await api('/admin/v1/mcp') }
async function action(work: () => Promise<void>) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await work() } catch (e: any) { error.value = e.message } finally { busy.value = false }
}
async function save() {
  await action(async () => { await post('/admin/v1/mcp', { request_id: crypto.randomUUID(), expected_revision: data.value.revision, policy: data.value.policy }); await load(); notice.value = '配置已保存。权限收紧立即生效，新增权限用于后续运行。' })
}
function add() {
  data.value.policy.services.push({ id: '', label: '', endpoint_ref: data.value.endpoints[0]?.ref || '', enabled: false,
    user_roles: ['admin'], agent_roles: ['single_agent', 'tool'], allowed_tools: [], timeout_seconds: 20 })
}
async function refresh(id: string) {
  await action(async () => { const result = await post('/admin/v1/mcp/refresh', { service_id: id }); if (result.status !== 'succeeded') throw new Error(result.error_code); await load(); notice.value = `已发现 ${result.tool_count} 个工具，请选择允许使用的工具并保存。` })
}
function catalog(id: string) { return data.value.catalogs.find((item: any) => item.service_id === id)?.tools || [] }
onMounted(() => action(load))
</script>

<template>
  <section class="mcp-panel">
    <h2>MCP 服务</h2>
    <p>管理 Agent 可发现和调用的外部工具。服务地址及密钥由部署环境配置，此处只引用已登记的连接。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="data">
      <div class="mcp-actions"><label>可用范围 <select v-model="data.policy.mode" :disabled="busy"><option value="none">全部关闭</option><option value="selected">仅选中服务</option><option value="all">全部已授权服务</option></select></label>
        <button :disabled="busy || !data.endpoints.length" @click="add">添加服务</button><button :disabled="busy" @click="save">保存配置</button></div>
      <p v-if="!data.endpoints.length">尚未登记连接。配置部署环境中的 MCP endpoint registry 后，即可在这里添加服务。</p>
      <article v-for="(item, index) in data.policy.services" :key="index" class="mcp-service">
        <div class="mcp-fields"><label>服务标识<input v-model="item.id" :disabled="busy" placeholder="例如 inspection-tools" /></label>
          <label>显示名称<input v-model="item.label" :disabled="busy" /></label>
          <label>已登记连接<select v-model="item.endpoint_ref" :disabled="busy"><option v-for="endpoint in data.endpoints" :key="endpoint.ref" :value="endpoint.ref">{{ endpoint.ref }}{{ endpoint.ready ? '' : '（未就绪）' }}</option></select></label>
          <label>调用时限（秒）<input v-model.number="item.timeout_seconds" type="number" min="2" max="30" :disabled="busy" /></label></div>
        <div class="mcp-actions"><label><input v-model="item.enabled" type="checkbox" :disabled="busy" /> 启用</label>
          <label v-if="data.policy.mode === 'selected'"><input v-model="data.policy.selected" :value="item.id" type="checkbox" :disabled="busy || !item.id" /> 本次范围包含此服务</label>
          <button :disabled="busy || !item.id" @click="refresh(item.id)">检查连接并刷新工具</button>
          <button :disabled="busy" @click="data.policy.selected = data.policy.selected.filter((id: string) => id !== item.id); data.policy.services.splice(index, 1)">移除</button></div>
        <fieldset :disabled="busy"><legend>账号权限</legend><label><input v-model="item.user_roles" value="admin" type="checkbox" />管理员</label><label><input v-model="item.user_roles" value="user" type="checkbox" />普通用户</label></fieldset>
        <fieldset :disabled="busy"><legend>Agent 角色</legend><label v-for="role in roles" :key="role[0]"><input v-model="item.agent_roles" :value="role[0]" type="checkbox" />{{ role[1] }}</label></fieldset>
        <fieldset :disabled="busy"><legend>允许调用的工具</legend>
          <p v-if="!catalog(item.id).length">先保存服务，再检查连接；发现工具后勾选并保存，未勾选的工具不会交给 Agent。</p>
          <div v-for="tool in catalog(item.id)" :key="tool.name" class="mcp-tool"><label><input v-model="item.allowed_tools" :value="tool.name" type="checkbox" /><strong>{{ tool.name }}</strong></label><p>{{ tool.description }}</p><details><summary>参数定义</summary><pre>{{ JSON.stringify(tool.input_schema, null, 2) }}</pre></details></div>
        </fieldset>
      </article>
      <h3>最近调用</h3><p v-if="!data.recent.length">暂无调用记录。</p><table v-else><thead><tr><th>工具</th><th>状态</th><th>时间</th><th>错误</th></tr></thead><tbody><tr v-for="row in data.recent" :key="row.job_id"><td>{{ row.tool }}</td><td>{{ row.status }}</td><td>{{ new Date(row.created_at).toLocaleString() }}</td><td>{{ row.error?.code || '—' }}</td></tr></tbody></table>
    </template>
  </section>
</template>

<style scoped>
.mcp-panel { overflow: auto; padding: 1.8rem; flex: 1; }
.mcp-actions, .mcp-fields { display: flex; flex-wrap: wrap; gap: 1rem; margin: 1rem 0; align-items: center; }
.mcp-fields label { display: grid; gap: .3rem; }
.mcp-service { border: 1px solid var(--border, #dce5dc); border-radius: .7rem; padding: 1rem; margin: 1rem 0; }
fieldset { border: 1px solid #dce5dc; margin: .8rem 0; } fieldset > label { display: inline-flex; gap: .3rem; margin: .4rem .8rem .4rem 0; }
.mcp-tool { margin: .6rem 0; border-bottom: 1px solid #e5ebe5; padding-bottom: .6rem; } .mcp-tool p { margin: .3rem 0; }
pre { overflow: auto; max-height: 20rem; white-space: pre-wrap; } table { width: 100%; text-align: left; } td, th { padding: .5rem; }
</style>
