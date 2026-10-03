<script setup lang="ts">
import { rememberFilters } from './page-filters'
import { computed, onMounted, ref } from 'vue'
import { api, post } from './api'
import ResourceDrawer from './ResourceDrawer.vue'
import UiIcon from './UiIcon.vue'
const data=ref<any>(null), busy=ref(false), error=ref(''), notice=ref(''), search=ref(''), toolSearch=ref('')
const editing=ref(false), scopeOpen=ref(false), policy=ref<any>(null), index=ref(0), revision=ref(0), tab=ref('connection')
const roles=[['single_agent','单 Agent'],['tool','工具 Agent'],['rag','知识 Agent'],['sqlbot','数据 Agent'],['vision','视觉 Agent']]
const item=computed(()=>policy.value?.services[index.value])
const services=computed(()=>(data.value?.policy.services || []).filter((s:any)=>`${s.label} ${s.id}`.toLowerCase().includes(search.value.toLowerCase())))
const modes:Record<string,string>={none:'全部关闭',selected:'仅选中服务',all:'全部已授权服务'}
async function load(){data.value=await api('/admin/v1/mcp')}
async function action(work:()=>Promise<void>){busy.value=true;error.value='';notice.value='';try{await work()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
function clone(){policy.value=JSON.parse(JSON.stringify(data.value.policy));revision.value=data.value.revision;error.value=''}
function edit(service?:any){clone();tab.value='connection';toolSearch.value='';if(service){index.value=policy.value.services.findIndex((s:any)=>s.id===service.id)}else{policy.value.services.push({id:'',label:'',endpoint_ref:data.value.endpoints[0]?.ref || '',enabled:false,user_roles:['admin'],agent_roles:['single_agent','tool'],allowed_tools:[],timeout_seconds:20});index.value=policy.value.services.length-1}editing.value=true}
function scope(){clone();scopeOpen.value=true}
async function save(){await action(async()=>{await post('/admin/v1/mcp',{request_id:crypto.randomUUID(),expected_revision:revision.value,policy:policy.value});await load();editing.value=false;scopeOpen.value=false;notice.value='配置已保存。权限收紧立即生效，新增权限用于后续运行。'})}
function catalog(id:string){return data.value?.catalogs.find((c:any)=>c.service_id===id)?.tools || []}
async function refresh(id:string){await action(async()=>{const result=await post('/admin/v1/mcp/refresh',{service_id:id});if(result.status!=='succeeded')throw new Error(result.error_code);await load();notice.value=`已发现 ${result.tool_count} 个工具。进入服务编辑，勾选允许调用的工具并保存。`})}
async function remove(){
  const candidate=JSON.parse(JSON.stringify(policy.value)), id=item.value.id
  candidate.selected=candidate.selected.filter((s:string)=>s!==id)
  candidate.services.splice(index.value,1)
  await action(async()=>{await post('/admin/v1/mcp',{request_id:crypto.randomUUID(),expected_revision:revision.value,policy:candidate});await load();editing.value=false;notice.value='服务已移除，调用权限已撤销。'})
}
onMounted(()=>action(load))
rememberFilters('MCPPanel', {search})
</script>
<template>
<section class="mcp-panel"><div class="page-title"><div><h1>MCP 服务</h1><p class="muted">连接外部工具，按账号与 Agent 角色授予调用权限。</p></div><button class="primary" :disabled="busy || !data?.endpoints.length" @click="edit()">＋ 添加服务</button></div>
<p v-if="error" class="error" role="alert">{{error}} <button v-if="!data" class="text-button" @click="action(load)">重试</button></p><p v-if="notice" class="notice" role="status">{{notice}}</p>
<div v-if="busy && !data" class="loading-state">正在加载服务…</div>
<template v-if="data"><div class="page-toolbar"><input v-model="search" type="search" placeholder="搜索服务名称或标识" aria-label="搜索 MCP 服务"/><button class="secondary" :disabled="busy" @click="scope">可用范围：{{modes[data.policy.mode]}}</button><span class="result-count">{{services.length}} 个服务</span></div>
<div v-if="!services.length" class="empty-card"><strong>{{search ? '没有匹配的服务' : '尚未添加 MCP 服务'}}</strong><p>{{data.endpoints.length ? '添加已登记的连接，保存后检查连接并选择工具。' : '尚未登记连接，请先配置部署环境中的 MCP 连接。'}}</p></div>
<div class="resource-grid"><article v-for="service in services" :key="service.id" class="resource-card"><div class="card-heading"><span class="resource-icon"><UiIcon name="mcp"/></span><div><h3>{{service.label || service.id}}</h3><span class="small muted">{{service.id}}</span></div></div><p class="card-description">{{catalog(service.id).length}} 个已发现工具 · {{service.allowed_tools.length}} 个获准调用</p><div class="card-meta"><span class="status-pill" :class="{neutral:!service.enabled}">{{service.enabled ? '已启用':'未启用'}}</span><span>{{data.policy.mode==='none' || (data.policy.mode==='selected' && !data.policy.selected.includes(service.id)) ? '不在可用范围':'在可用范围'}}</span><span>账号：{{service.user_roles.map((r:string)=>r==='admin'?'管理员':'普通用户').join('、')}}</span></div><div class="card-actions"><button class="secondary" :disabled="busy" @click="edit(service)">配置与工具</button><button class="text-button" :disabled="busy" @click="refresh(service.id)">检查连接</button></div></article></div>
<details class="panel-card"><summary>最近调用 · {{data.recent.length}} 条</summary><p v-if="!data.recent.length" class="muted">暂无调用记录。</p><div v-else class="table-card"><table><thead><tr><th>工具</th><th>结果</th><th>时间</th><th>原因</th></tr></thead><tbody><tr v-for="row in data.recent" :key="row.job_id"><td>{{row.tool}}</td><td>{{row.status==='succeeded'?'成功':row.status==='failed'?'失败':row.status}}</td><td>{{new Date(row.created_at).toLocaleString()}}</td><td>{{row.error?.code || '—'}}</td></tr></tbody></table></div></details></template>
<ResourceDrawer v-model="editing" :title="item?.label || '添加 MCP 服务'" :snapshot="policy" :busy="busy" :error="error">
<template v-if="item"><div class="tabs" role="tablist" aria-label="服务配置"><button v-for="[key,label] in [['connection','连接信息'],['permissions','权限范围'],['tools','工具目录']]" :key="key" :aria-selected="tab===key" @click="tab=key" role="tab">{{label}}</button></div>
<form id="mcp-editor" @submit.prevent="save"><fieldset :disabled="busy"><section v-show="tab==='connection'"><p class="muted small">地址与密钥由部署环境管理。连接检查只在点击检查按钮时发起。</p><label>服务标识<input v-model="item.id" required :readonly="data.policy.services.some((s:any)=>s.id===item.id)" placeholder="例如 inspection-tools"/></label><label>显示名称<input v-model="item.label" required/></label><label>已登记连接<select v-model="item.endpoint_ref" required><option v-for="endpoint in data.endpoints" :key="endpoint.ref" :value="endpoint.ref">{{endpoint.ref}}{{endpoint.ready?'':'（未就绪）'}}</option></select></label><label>调用时限（秒）<input v-model.number="item.timeout_seconds" type="number" min="2" max="30" required/></label><label class="checkbox"><input v-model="item.enabled" type="checkbox"/>启用此服务</label><label v-if="policy.mode==='selected'" class="checkbox"><input v-model="policy.selected" :value="item.id" type="checkbox" :disabled="!item.id"/>纳入可用范围</label><p class="small muted">移除需在下方确认保存后生效。</p><details><summary class="danger">移除服务</summary><p>保存后将撤销此服务的调用权限。</p><button type="button" class="secondary danger" :disabled="busy" @click="remove">确认移除并保存</button></details></section>
<section v-show="tab==='permissions'"><h3>账号权限</h3><div class="choices"><label><input v-model="item.user_roles" value="admin" type="checkbox"/>管理员</label><label><input v-model="item.user_roles" value="user" type="checkbox"/>普通用户</label></div><h3 class="section-heading">Agent 角色</h3><div class="choices"><label v-for="role in roles" :key="role[0]"><input v-model="item.agent_roles" :value="role[0]" type="checkbox"/>{{role[1]}}</label></div></section>
<section v-show="tab==='tools'"><input v-model="toolSearch" type="search" placeholder="搜索工具" aria-label="搜索工具"/><p class="small muted">只向已授权的 Agent 提供勾选工具。</p><p v-if="!catalog(item.id).length">保存服务后，在目录点击“检查连接”，再编辑并授权工具。</p><article v-for="tool in catalog(item.id).filter((t:any)=>(t.name+' '+t.description).toLowerCase().includes(toolSearch.toLowerCase()))" :key="tool.name" class="panel-card"><label class="checkbox"><input v-model="item.allowed_tools" :value="tool.name" type="checkbox"/><strong>{{tool.name}}</strong></label><p class="muted small">{{tool.description}}</p><details><summary>参数定义</summary><pre>{{JSON.stringify(tool.input_schema,null,2)}}</pre></details></article><p v-for="missing in item.allowed_tools.filter((name:string)=>!catalog(item.id).some((t:any)=>t.name===name))" :key="missing" class="notice">{{missing}} 不在最近发现的目录中，请核对后保存。</p></section></fieldset></form></template>
<template #footer><button type="submit" class="primary" form="mcp-editor" :disabled="busy">{{busy?'正在保存…':'保存配置'}}</button></template>
</ResourceDrawer>
<ResourceDrawer v-model="scopeOpen" title="MCP 可用范围" :snapshot="policy" :busy="busy" :error="error" width="540px"><template v-if="policy"><label>服务范围<select v-model="policy.mode"><option v-for="(label,key) in modes" :key="key" :value="key">{{label}}</option></select></label><div v-if="policy.mode==='selected'" class="choices"><label v-for="s in policy.services" :key="s.id"><input type="checkbox" v-model="policy.selected" :value="s.id"/>{{s.label || s.id}}</label></div><p class="muted">账号和 Agent 权限仍分别校验；选入范围不会扩大已有授权。</p></template><template #footer><button class="primary" :disabled="busy" @click="save">保存范围</button></template></ResourceDrawer>
</section>
</template>
