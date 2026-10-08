<script setup lang="ts">
import RefreshDataButton from './RefreshDataButton.vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { api, post } from './api'
import { rememberFilters } from './page-filters'
import { emptyConnection, connectionHeaders, parseMcpImport, mcpError } from './mcp-config'
import ResourceDrawer from './ResourceDrawer.vue'
import UiIcon from './UiIcon.vue'
const data=ref<any>(null), busy=ref(false), error=ref(''), notice=ref(''), search=ref(''), toolSearch=ref('')
const editing=ref(false), scopeOpen=ref(false), policy=ref<any>(null), index=ref(0), revision=ref(0), tab=ref('connection')
const connectionMode=ref('remote'), connection=ref(emptyConnection()), originalConnection=ref(''), previousRef=ref('')
const importText=ref(''), imported=ref<ReturnType<typeof parseMcpImport>>([]), importIndex=ref(0)
const editorDrawer=ref<InstanceType<typeof ResourceDrawer>|null>(null)
const roles=[['single_agent','单 Agent'],['tool','工具 Agent'],['rag','知识 Agent'],['sqlbot','数据 Agent'],['vision','视觉 Agent']]
const item=computed(()=>policy.value?.services[index.value])
const services=computed(()=>(data.value?.policy.services || []).filter((s:any)=>`${s.label} ${s.id}`.toLowerCase().includes(search.value.toLowerCase())))
const modes:Record<string,string>={none:'全部关闭',selected:'仅选中服务',all:'全部已授权服务'}
const savedEndpoint=computed(()=>data.value?.endpoints.find((e:any)=>e.ref===previousRef.value))
const connectionChanged=computed(()=>connectionMode.value==='remote' && (!previousRef.value || JSON.stringify(connection.value)!==originalConnection.value))
function catalog(id:string){return data.value?.catalogs.find((c:any)=>c.service_id===id && c.endpoint_ref===item.value?.endpoint_ref)?.tools || []}
function count(id:string){return data.value?.catalogs.find((c:any)=>c.service_id===id)?.tools.length || 0}
async function load(){data.value=await api('/admin/v1/mcp')}
async function action(work:()=>Promise<void>){busy.value=true;error.value='';notice.value='';try{await work()}catch(e){error.value=mcpError((e as Error).message)}finally{busy.value=false}}
function clone(){policy.value=JSON.parse(JSON.stringify(data.value.policy));revision.value=data.value.revision;error.value=''}
function edit(service?:any){
  clone();tab.value='connection';toolSearch.value='';connection.value=emptyConnection();previousRef.value=''
  if(service){
    index.value=policy.value.services.findIndex((s:any)=>s.id===service.id)
    const endpoint=data.value.endpoints.find((e:any)=>e.ref===service.endpoint_ref)
    connectionMode.value=endpoint?.managed?'remote':'deployment'
    if(endpoint?.managed){previousRef.value=endpoint.ref;connection.value={...emptyConnection(),url:endpoint.url || '',transport:endpoint.transport || 'streamable_http',auth_type:endpoint.auth_type || 'none',data_origin:endpoint.data_origin || 'public',header_name:endpoint.header_names?.[0] || 'X-API-Key'}}
  }else{
    connectionMode.value='remote';policy.value.services.push({id:'mcp-'+crypto.randomUUID().slice(0,8),label:'',endpoint_ref:'',enabled:false,user_roles:['admin'],agent_roles:['single_agent','tool'],allowed_tools:[],timeout_seconds:20,usage_instructions:''});index.value=policy.value.services.length-1
  }
  originalConnection.value=JSON.stringify(connection.value);editing.value=true
}
function parseImport(){error.value='';try{imported.value=parseMcpImport(importText.value);importIndex.value=0;importText.value=''}catch(e){error.value=(e as Error).message}}
function applyImport(){const entry=imported.value[importIndex.value];if(!entry)return;item.value.label=entry.label;connection.value=entry.connection;connectionMode.value='remote';imported.value=[];notice.value='已填入配置，请检查后保存并连接。'}
function scope(){clone();scopeOpen.value=true}
async function saveScope(){await action(async()=>{await post('/admin/v1/mcp',{request_id:crypto.randomUUID(),expected_revision:revision.value,policy:policy.value});await load();scopeOpen.value=false;notice.value='可用范围已保存。'})}
async function save(connect=false){await action(async()=>{
  const candidate=JSON.parse(JSON.stringify(policy.value)), service=candidate.services[index.value]
  const changed=connectionChanged.value || (connectionMode.value==='deployment' && service.endpoint_ref!==data.value.policy.services.find((s:any)=>s.id===service.id)?.endpoint_ref)
  let write:any=null
  if(connectionChanged.value){
    const ref='web-'+crypto.randomUUID().replaceAll('-',''), {url,transport,auth_type,data_origin}=connection.value
    write={ref,previous_ref:previousRef.value || null,url:url.trim(),transport,auth_type,data_origin,headers:connectionHeaders(connection.value)}
    service.endpoint_ref=ref;service.enabled=false;service.allowed_tools=[]
  }
  if(changed){service.enabled=false;service.allowed_tools=[]}
  if(!service.label.trim())throw new Error('请填写服务名称。')
  if(!service.endpoint_ref)throw new Error('请选择连接或填写服务 URL。')
  await post('/admin/v1/mcp',{request_id:crypto.randomUUID(),expected_revision:revision.value,policy:candidate,connection:write});await load()
  if(connect || changed){
    edit(data.value.policy.services.find((s:any)=>s.id===service.id))
    await nextTick();editorDrawer.value?.markClean()
    const result=await post('/admin/v1/mcp/refresh',{service_id:service.id})
    if(result.status!=='succeeded')throw new Error(result.error_code)
    await load();edit(data.value.policy.services.find((s:any)=>s.id===service.id));tab.value='tools'
    await nextTick();editorDrawer.value?.markClean()
    notice.value=`连接成功，发现 ${result.tool_count} 个工具。勾选工具、启用服务并保存即可使用。`
  }else{editing.value=false;notice.value='配置已保存。权限收紧立即生效，新增权限用于后续运行。'}
})}
async function refresh(id:string){await action(async()=>{const result=await post('/admin/v1/mcp/refresh',{service_id:id});if(result.status!=='succeeded')throw new Error(result.error_code);await load();notice.value=`连接成功，同步 ${result.tool_count} 个工具；原有授权保持不变。`})}
async function remove(){await action(async()=>{const candidate=JSON.parse(JSON.stringify(policy.value)), id=item.value.id;candidate.selected=candidate.selected.filter((s:string)=>s!==id);candidate.services.splice(index.value,1);await post('/admin/v1/mcp',{request_id:crypto.randomUUID(),expected_revision:revision.value,policy:candidate});await load();editing.value=false;notice.value='服务已移除，调用权限已撤销。'})}
watch(editing,open=>{if(!open){connection.value=emptyConnection();originalConnection.value='';importText.value='';imported.value=[];previousRef.value=''}})
onMounted(()=>action(load));rememberFilters('MCPPanel',{search})
</script>
<template>
<section class="mcp-panel">
  <div class="page-title"><div><h1>MCP 服务</h1><p class="muted">接入外部工具，选择用途和权限，在对话中按需调用。</p></div><div class="row-actions"><RefreshDataButton :refresh="()=>action(load)" :disabled="busy || editing || scopeOpen"/><button class="primary" :disabled="busy || !data" @click="edit()">＋ 添加服务</button></div></div>
  <p v-if="error && !editing" class="error" role="alert">{{error}} <button v-if="!data" class="text-button" @click="action(load)">重试</button></p><p v-if="notice && !editing" class="notice" role="status">{{notice}}</p>
  <div v-if="busy && !data" class="loading-state">正在加载服务…</div>
  <template v-if="data">
    <div class="page-toolbar"><input v-model="search" type="search" placeholder="搜索服务名称或标识" aria-label="搜索 MCP 服务"/><button class="secondary" :disabled="busy" @click="scope">可用范围：{{modes[data.policy.mode]}}</button><span class="result-count">{{services.length}} 个服务</span></div>
    <div v-if="!services.length" class="empty-card"><strong>{{search?'没有匹配的服务':'接入第一个 MCP 服务'}}</strong><p>填写远程服务地址，或导入平台提供的 JSON 配置。</p></div>
    <div class="resource-grid"><article v-for="service in services" :key="service.id" class="resource-card">
      <div class="card-heading"><span class="resource-icon"><UiIcon name="mcp"/></span><div><h3>{{service.label || service.id}}</h3><span class="small muted">{{service.id}}</span></div></div>
      <p class="card-description">{{service.usage_instructions || '尚未填写使用说明'}}</p><p class="small muted">{{count(service.id)}} 个已发现工具 · {{service.allowed_tools.length}} 个获准调用</p>
      <div class="card-meta"><span class="status-pill" :class="{neutral:!service.enabled}">{{service.enabled?'已启用':'未启用'}}</span><span>{{data.policy.mode==='none' || (data.policy.mode==='selected' && !data.policy.selected.includes(service.id))?'不在可用范围':'在可用范围'}}</span></div>
      <div class="card-actions"><button class="secondary" :disabled="busy" @click="edit(service)">配置与工具</button><button class="text-button" :disabled="busy" @click="refresh(service.id)">检查连接</button></div>
    </article></div>
    <details class="panel-card"><summary>最近调用 · {{data.recent.length}} 条</summary><p v-if="!data.recent.length" class="muted">暂无调用记录。</p><div v-else class="table-card"><table><thead><tr><th>工具</th><th>结果</th><th>时间</th><th>原因</th></tr></thead><tbody><tr v-for="row in data.recent" :key="row.job_id"><td>{{row.tool}}</td><td>{{row.status==='succeeded'?'成功':row.status==='failed'?'失败':row.status}}</td><td>{{new Date(row.created_at).toLocaleString()}}</td><td>{{mcpError(row.error?.code || '—')}}</td></tr></tbody></table></div></details>
  </template>
  <ResourceDrawer ref="editorDrawer" v-model="editing" :title="item?.label || '添加 MCP 服务'" :snapshot="{policy,connection,connectionMode}" :busy="busy" :error="error" width="780px">
    <template v-if="item">
      <p v-if="notice" class="notice" role="status">{{notice}}</p>
      <div class="tabs" role="tablist" aria-label="服务配置"><button v-for="[key,label] in [['connection','连接信息'],['tools','工具与用途'],['permissions','权限范围']]" :key="key" :aria-selected="tab===key" @click="tab=key" role="tab">{{label}}</button></div>
      <form id="mcp-editor" @submit.prevent="save()"><fieldset :disabled="busy">
        <section v-show="tab==='connection'">
          <details class="panel-card"><summary>从 JSON 配置导入</summary><p class="small muted">支持远程 mcpServers 配置，解析后选择服务，不会自动启用。</p><textarea v-model="importText" rows="6" aria-label="MCP JSON 配置" placeholder='{"mcpServers":{"my-service":{"url":"https://example.com/mcp"}}}' autocomplete="off" spellcheck="false"/><button type="button" class="secondary" @click="parseImport">解析配置</button><template v-if="imported.length"><label>选择服务<select v-model="importIndex"><option v-for="(entry,i) in imported" :key="i" :value="i">{{entry.label}}</option></select></label><button type="button" class="secondary" @click="applyImport">填入表单</button></template></details>
          <label>服务名称<input v-model="item.label" required maxlength="100" placeholder="例如：半导体资料检索"/></label>
          <label>连接方式<select v-model="connectionMode"><option value="remote">远程服务 URL</option><option v-if="data.endpoints.some((e:any)=>!e.managed)" value="deployment">已登记连接（含内网服务）</option></select></label>
          <template v-if="connectionMode==='remote'">
            <p v-if="data.web_connections_ready===false" class="error">服务器尚未配置 MCP 凭据加密密钥，暂不能保存远程连接。</p>
            <label>服务 URL<input v-model="connection.url" type="url" required placeholder="https://example.com/mcp" autocomplete="off"/></label><p class="small muted">由 SemiBrain 服务器连接。仅支持 HTTPS，认证信息填写到请求头。</p>
            <div class="fields"><label>传输协议<select v-model="connection.transport"><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></label><label>认证方式<select v-model="connection.auth_type"><option value="none">无认证</option><option value="bearer">Bearer Token</option><option value="api_key">API Key</option><option value="headers">自定义请求头</option></select></label></div>
            <template v-if="connection.auth_type==='bearer' || connection.auth_type==='api_key'"><label v-if="connection.auth_type==='api_key'">请求头名称<input v-model="connection.header_name" placeholder="X-API-Key"/></label><label>{{connection.auth_type==='bearer'?'Token':'API Key'}}<input v-model="connection.token" type="password" autocomplete="new-password" :placeholder="savedEndpoint?.authenticated?'已保存；留空保持原凭据':'填写认证凭据'"/></label></template>
            <template v-if="connection.auth_type==='headers'"><p class="small muted">{{savedEndpoint?.authenticated?'已保存请求头：'+savedEndpoint.header_names.join('、')+'。不添加则保留；添加后整体替换。':'填写服务要求的请求头。'}}</p><div v-for="(row,i) in connection.headers" :key="i" class="mcp-header-row"><input v-model="row.name" placeholder="请求头名称" aria-label="请求头名称"/><input v-model="row.value" type="password" placeholder="请求头值" aria-label="请求头值" autocomplete="new-password"/><button type="button" class="text-button" @click="connection.headers.splice(i,1)">移除</button></div><button type="button" class="secondary" @click="connection.headers.push({name:'',value:''})">＋ 请求头</button></template>
            <label>数据来源<select v-model="connection.data_origin"><option value="public">公开资料</option><option value="authorized_business">授权业务数据</option><option value="synthetic">合成演示数据</option></select></label>
          </template>
          <label v-else>已登记连接<select v-model="item.endpoint_ref" required><option value="" disabled>选择连接</option><option v-for="endpoint in data.endpoints.filter((e:any)=>!e.managed)" :key="endpoint.ref" :value="endpoint.ref">{{endpoint.ref}}{{endpoint.ready?'':'（未就绪）'}}</option></select></label>
          <label>调用时限（秒）<input v-model.number="item.timeout_seconds" type="number" min="2" max="30" required/></label><p class="small muted">服务标识：{{item.id}}。连接变更后需重新同步并授权。</p>
          <button type="button" class="secondary" @click="save(true)">保存并连接 / 同步工具</button>
          <details v-if="data.policy.services.some((s:any)=>s.id===item.id)" class="mcp-remove"><summary class="danger">移除服务</summary><p>移除后撤销调用权限。</p><button type="button" class="secondary danger" @click="remove">确认移除并保存</button></details>
        </section>
        <section v-show="tab==='tools'">
          <label>使用说明<textarea v-model="item.usage_instructions" rows="4" maxlength="4000" placeholder="说明服务用途、适用问题和限制，帮助 Agent 选择工具。"/></label>
          <label class="checkbox"><input v-model="item.enabled" type="checkbox" :disabled="!catalog(item.id).length || connectionChanged"/>启用此服务</label><label v-if="policy.mode==='selected'" class="checkbox"><input v-model="policy.selected" :value="item.id" type="checkbox"/>纳入可用范围</label><p v-if="policy.mode==='none'" class="notice">当前 MCP 全部关闭，请在列表的“可用范围”中开启。</p>
          <input v-model="toolSearch" type="search" placeholder="搜索工具" aria-label="搜索工具"/><p class="small muted">只允许调用勾选工具；同步目录不会自动授权新工具。</p><p v-if="!catalog(item.id).length">请先保存并连接，再选择工具。</p>
          <label v-for="name in item.allowed_tools.filter((name:string)=>!catalog(item.id).some((t:any)=>t.name===name))" :key="name" class="checkbox"><input v-model="item.allowed_tools" :value="name" type="checkbox"/>{{name}}（目录中已移除，请取消授权）</label>
          <article v-for="tool in catalog(item.id).filter((t:any)=>(t.name+' '+t.description).toLowerCase().includes(toolSearch.toLowerCase()))" :key="tool.name" class="panel-card"><label class="checkbox"><input v-model="item.allowed_tools" :value="tool.name" type="checkbox"/><strong>{{tool.name}}</strong></label><p class="muted small">{{tool.description}}</p><details><summary>参数定义</summary><pre>{{JSON.stringify(tool.input_schema,null,2)}}</pre></details></article>
        </section>
        <section v-show="tab==='permissions'"><h3>账号权限</h3><div class="choices"><label><input v-model="item.user_roles" value="admin" type="checkbox"/>管理员</label><label><input v-model="item.user_roles" value="user" type="checkbox"/>普通用户</label></div><h3 class="section-heading">Agent 角色</h3><div class="choices"><label v-for="role in roles" :key="role[0]"><input v-model="item.agent_roles" :value="role[0]" type="checkbox"/>{{role[1]}}</label></div></section>
      </fieldset></form>
    </template>
    <template #footer><button type="submit" class="primary" form="mcp-editor" :disabled="busy">{{busy?'正在处理…':'保存配置'}}</button></template>
  </ResourceDrawer>
  <ResourceDrawer v-model="scopeOpen" title="MCP 可用范围" :snapshot="policy" :busy="busy" :error="error" width="540px"><template v-if="policy"><label>服务范围<select v-model="policy.mode"><option v-for="(label,key) in modes" :key="key" :value="key">{{label}}</option></select></label><div v-if="policy.mode==='selected'" class="choices"><label v-for="s in policy.services" :key="s.id"><input type="checkbox" v-model="policy.selected" :value="s.id"/>{{s.label || s.id}}</label></div><p class="muted">账号和 Agent 权限仍分别校验；选入范围不会扩大已有授权。</p></template><template #footer><button class="primary" :disabled="busy" @click="saveScope">保存范围</button></template></ResourceDrawer>
</section>
</template>
<style scoped>
.mcp-header-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr) auto;gap:8px;align-items:center;margin-bottom:8px}.mcp-header-row input{margin:0}.mcp-remove{margin-top:24px}.card-description{white-space:pre-wrap;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}pre{white-space:pre-wrap;overflow-wrap:anywhere}.fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:600px){.fields{grid-template-columns:1fr}.mcp-header-row{grid-template-columns:minmax(0,1fr) auto}.mcp-header-row input:first-child{grid-column:1/-1}}
</style>
