<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from './api'
import ResourceDrawer from './ResourceDrawer.vue'
import { ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message-box/style/css'
const searchDraft=ref<any>(null), searchRevision=ref(0)
function editSearch(){searchDraft.value=JSON.parse(JSON.stringify(search.value.policy));searchRevision.value=search.value.revision;error.value='';networkOpen.value=true}
const tab=ref('search'), networkOpen=ref(false), expiredWorkers=ref(false)
async function confirmDrain(item:any){try{await ElMessageBox.confirm(item.draining?'恢复后 Worker 将领取队列中的新任务。':'暂停只停止领取新任务，在途任务继续完成。',item.draining?'恢复领取任务？':'暂停领取新任务？',{confirmButtonText:'确认',cancelButtonText:'取消',type:'warning'});await drain(item)}catch{}}

const search = ref<any>(null), queues = ref<any[]>([]), error = ref(''), notice = ref(''), busy = ref(false)
const names: Record<string, string> = { bocha: '博查', zhipu: '智谱 Search Pro', conversation: '会话服务', agent: 'Agent 服务', business: '业务服务' }
const recoveryReason = ref(''), recoveryKey = ref('')
async function recover(service?: string, identity?: string) {
  busy.value = true; error.value = ''; notice.value = ''
  if (!recoveryKey.value) recoveryKey.value = crypto.randomUUID()
  try {
    await post(service ? `/admin/v1/operations/${service}/quarantine/${encodeURIComponent(identity!)}/redrive` : '/admin/v1/operations/recover-transport',
      { request_id: recoveryKey.value, reason: recoveryReason.value })
    notice.value = service ? '事件已重新排队，原事件标识和执行尝试次数保留。' : '各服务已重新投递持久化事件，已消费事件会自动去重。'
    recoveryKey.value = ''; await refresh()
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function refresh() {
  busy.value = true; error.value = ''
  const results = await Promise.allSettled([api('/admin/v1/search'), api('/admin/v1/operations/queues')])
  if (results[0].status === 'fulfilled') search.value = results[0].value
  else { search.value = null; error.value = String(results[0].reason) }
  if (results[1].status === 'fulfilled') queues.value = results[1].value.items
  else { queues.value = []; error.value += ' ' + String(results[1].reason) }
  busy.value = false
}
async function save() {
  if (!search.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await post('/admin/v1/search', { request_id: crypto.randomUUID(), expected_revision: searchRevision.value, policy: searchDraft.value }); networkOpen.value=false;notice.value = '搜索配置已发布。新任务使用新版本，禁用供应商立即阻止后续外发。'; await refresh() }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function probe(provider: string) {
  busy.value = true; error.value = ''; notice.value = ''
  try { const result = await post('/admin/v1/search/probe', { request_id: crypto.randomUUID(), provider }); notice.value = `${names[provider]}：${result.status === 'succeeded' ? '连接成功，返回 ' + result.result_count + ' 条候选资料' : result.error_code || result.status}，耗时 ${result.elapsed_ms ?? '未知'} ms`; await refresh() }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function drain(item: any) {
  busy.value = true; error.value = ''; notice.value = ''
  try { await post(`/admin/v1/operations/${item.service}/drain`, { request_id: crypto.randomUUID(), expected_revision: item.revision, draining: !item.draining }); notice.value = item.draining ? '已恢复领取新任务。' : '已暂停领取新任务；在途任务继续完成，队列中的任务保留。'; await refresh() }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
onMounted(refresh)
</script>
<template>
  <section class="resource-panel governance-panel">
    <div class="page-title"><h1>运行治理</h1><button class="secondary" :disabled="busy" @click="refresh">刷新状态</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="busy && !search && !queues.length" class="loading-state">正在读取运行状态…</p><div class="tabs" role="tablist" aria-label="治理分类"><button v-for="[key,label] in [['search','网络搜索'],['workers','Worker 与队列'],['events','运行事件']]" :key="key" role="tab" :aria-selected="tab===key" @click="tab=key">{{label}}</button></div>
    <section v-if="search" v-show="tab==='search'"><div class="page-title"><div><h2>搜索来源</h2><p class="muted">显式检查连接；打开页面不会自动发起搜索。</p></div><button class="primary" :disabled="busy" @click="editSearch">编辑搜索配置</button></div><div class="resource-grid"><article v-for="provider in search.providers" :key="provider.name" class="resource-card"><h3>{{names[provider.name]}}</h3><div class="card-meta"><span class="status-pill">{{search.policy.providers[provider.name].enabled ? '已启用':'已停用'}}</span><span>{{provider.credential_configured ? '凭据已配置':'凭据未配置'}}</span></div><p class="card-description">今日请求 {{provider.requests_today}} 次 · 日限额 {{search.policy.providers[provider.name].daily_limit ?? '未设置'}}</p><button class="secondary" :disabled="busy || !provider.credential_configured" @click="probe(provider.name)">测试连接</button></article></div></section>
    <ResourceDrawer v-model="networkOpen" title="网络搜索配置" width="960px" :snapshot="searchDraft" :busy="busy" :error="error"><form v-if="searchDraft" id="search-editor" @submit.prevent="save"><h2>网络搜索</h2><p class="muted">配置版本 {{ searchRevision }} · 用量统计日 {{ search.day_utc }}（UTC）。连接测试会产生一次真实搜索请求，也计入限额。密钥由服务端配置。</p>
      <div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>来源</th><th>启用</th><th>凭据状态</th><th>今日请求</th><th>日限额（留空不限制）</th><th>操作</th></tr></thead><tbody><tr v-for="provider in search.providers" :key="provider.name"><td>{{ names[provider.name] }}</td><td><input type="checkbox" v-model="searchDraft.providers[provider.name].enabled" :aria-label="'启用' + names[provider.name]" :disabled="busy" /></td><td>{{ provider.credential_configured ? '已配置' : '未配置' }}</td><td>{{ provider.requests_today }}</td><td><input type="number" min="1" max="1000000" :value="searchDraft.providers[provider.name].daily_limit" @input="searchDraft.providers[provider.name].daily_limit = ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : null" :aria-label="names[provider.name] + '日限额'" :disabled="busy" /></td><td><button class="secondary" :disabled="busy || !provider.credential_configured" @click="probe(provider.name)">测试连接</button></td></tr></tbody></table></div>
      <div class="row-actions"><label>默认来源 <select :value="searchDraft.order[0]" @change="searchDraft.order = ($event.target as HTMLSelectElement).value === 'bocha' ? ['bocha','zhipu'] : ['zhipu','bocha']" :disabled="busy"><option value="bocha">博查</option><option value="zhipu">智谱 Search Pro</option></select></label><label>搜索超时（秒） <input type="number" v-model.number="searchDraft.timeout_seconds" min="2" max="20" :disabled="busy" /></label></div>
      <details><summary>最近 20 次供应商请求</summary><div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>时间</th><th>供应商 / 配置版本</th><th>状态 / 原因</th><th>候选数</th><th>耗时</th></tr></thead><tbody><tr v-for="(item, index) in search.recent" :key="index"><td>{{ item.created_at }}</td><td>{{ names[item.provider] }} / {{ item.revision }}</td><td>{{ item.status }} {{ item.error_code || '' }}</td><td>{{ item.result_count ?? '—' }}</td><td>{{ item.elapsed_ms ?? '—' }} ms</td></tr></tbody></table></div></details>
    </form><template #footer><button type="submit" form="search-editor" class="primary" :disabled="busy">发布搜索配置</button></template>
    </ResourceDrawer><section v-show="tab==='workers' || tab==='events'"><h2>{{tab==='workers'?'队列与 Worker':'事件恢复与异常'}}</h2><p class="muted">任务数来自持久记录。暂停只停止领取新任务，在途执行与状态对账继续；恢复不会清空已用次数或创建重复任务。</p>
      <details v-if="tab==='events'" open><summary>事件恢复操作</summary><p>用于事件中断后的对账恢复。不会重新运行已完成任务；失败后重试沿用同一操作编号。</p><label>操作原因 <input v-model="recoveryReason" maxlength="300" @input="recoveryKey = ''" /></label><button class="secondary" :disabled="busy || recoveryReason.trim().length < 5" @click="recover()">恢复事件传输</button></details>
      <article v-for="item in queues" :key="item.service" class="queue-card"><div class="page-title"><h3>{{ names[item.service] }}</h3><button v-if="item.service !== 'conversation' && !item.unavailable" class="secondary" :disabled="busy" @click="confirmDrain(item)">{{ item.draining ? '恢复领取任务' : '暂停领取新任务' }}</button></div>
        <p v-if="item.unavailable" class="error">服务暂时不可达，无法核验状态。</p><template v-else><p>{{ item.draining ? '已暂停领取' : '正常领取' }} · 待投递事件 {{ item.pending_outbox }} · 隔离事件 {{ item.quarantined_events }}</p><label v-if="tab==='workers'" class="checkbox small muted"><input type="checkbox" v-model="expiredWorkers"/>显示心跳已过期的 Worker</label><p v-for="worker in item.workers.filter((w:any)=>w.online || expiredWorkers)" v-show="tab==='workers'" :key="worker.id" class="small muted">Worker {{ worker.id }} · {{ worker.online ? '在线' : '心跳已过期' }} · {{ worker.last_seen }}</p><p v-if="tab==='workers' && !item.workers.some((w:any)=>w.online)" class="muted">尚无近期 Worker 心跳，不能确认在线。</p>
          <div v-for="queue in item.queues" v-show="tab==='workers'" :key="queue.name"><h4>{{ queue.name }}</h4><p>排队 {{ queue.counts.queued }} · 在途 {{ queue.counts.running }} · 失败 {{ queue.counts.failed }} · 过期租约 {{ queue.expired_leases }} · 最长等待 {{ queue.oldest_wait_seconds ?? '—' }} 秒</p><details><summary>最近待处理和失败任务</summary><ul><li v-for="job in queue.recent" :key="job.id"><code>{{ job.id }}</code> · {{ job.status }} · 第 {{ job.attempt ?? 0 }} 次尝试</li></ul></details></div>
          <details v-if="tab==='events' && item.quarantine?.length" open><summary>隔离事件（最多 100 条）</summary><ul><li v-for="event in item.quarantine" :key="event.id"><code>{{ event.id }}</code> · {{ event.reason }} · {{ event.state }}<button v-if="item.service !== 'business'" class="secondary" :disabled="busy || event.state !== 'pending' || (event.redrives || 0) >= 2 || recoveryReason.trim().length < 5" @click="recover(item.service,event.id)">重新排队</button></li></ul><p class="small muted">请在事件恢复操作中填写原因；每个事件最多人工重试两次，不显示事件正文。</p></details>
        </template></article>
    </section>
  </section>
</template>
