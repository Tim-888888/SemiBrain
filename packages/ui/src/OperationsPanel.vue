<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from './api'
const search = ref<any>(null), queues = ref<any[]>([]), error = ref(''), notice = ref(''), busy = ref(false)
const names: Record<string, string> = { bocha: '博查', zhipu: '智谱 Search Pro', conversation: '会话服务', agent: 'Agent 服务', business: '业务服务' }
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
  try { await post('/admin/v1/search', { request_id: crypto.randomUUID(), expected_revision: search.value.revision, policy: search.value.policy }); notice.value = '搜索配置已发布。新任务使用新版本，禁用供应商立即阻止后续外发。'; await refresh() }
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
    <section v-if="search"><h2>网络搜索</h2><p class="muted">配置版本 {{ search.revision }} · 用量统计日 {{ search.day_utc }}（UTC）。连接测试会产生一次真实搜索请求，也计入限额。密钥由服务端配置。</p>
      <div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>来源</th><th>启用</th><th>凭据状态</th><th>今日请求</th><th>日限额（留空不限制）</th><th>操作</th></tr></thead><tbody><tr v-for="provider in search.providers" :key="provider.name"><td>{{ names[provider.name] }}</td><td><input type="checkbox" v-model="search.policy.providers[provider.name].enabled" :aria-label="'启用' + names[provider.name]" :disabled="busy" /></td><td>{{ provider.credential_configured ? '已配置' : '未配置' }}</td><td>{{ provider.requests_today }}</td><td><input type="number" min="1" max="1000000" :value="search.policy.providers[provider.name].daily_limit" @input="search.policy.providers[provider.name].daily_limit = ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : null" :aria-label="names[provider.name] + '日限额'" :disabled="busy" /></td><td><button class="secondary" :disabled="busy || !provider.credential_configured" @click="probe(provider.name)">测试连接</button></td></tr></tbody></table></div>
      <div class="row-actions"><label>默认来源 <select :value="search.policy.order[0]" @change="search.policy.order = ($event.target as HTMLSelectElement).value === 'bocha' ? ['bocha','zhipu'] : ['zhipu','bocha']" :disabled="busy"><option value="bocha">博查</option><option value="zhipu">智谱 Search Pro</option></select></label><label>搜索超时（秒） <input type="number" v-model.number="search.policy.timeout_seconds" min="2" max="20" :disabled="busy" /></label><button class="primary" :disabled="busy" @click="save">发布搜索配置</button></div>
      <details><summary>最近 20 次供应商请求</summary><div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>时间</th><th>供应商 / 配置版本</th><th>状态 / 原因</th><th>候选数</th><th>耗时</th></tr></thead><tbody><tr v-for="(item, index) in search.recent" :key="index"><td>{{ item.created_at }}</td><td>{{ names[item.provider] }} / {{ item.revision }}</td><td>{{ item.status }} {{ item.error_code || '' }}</td><td>{{ item.result_count ?? '—' }}</td><td>{{ item.elapsed_ms ?? '—' }} ms</td></tr></tbody></table></div></details>
    </section>
    <section><h2>队列与 Worker</h2><p class="muted">任务数来自持久记录。暂停只停止领取新任务，在途执行与状态对账继续；恢复不会清空已用次数或创建重复任务。</p>
      <article v-for="item in queues" :key="item.service" class="queue-card"><div class="page-title"><h3>{{ names[item.service] }}</h3><button v-if="item.service !== 'conversation' && !item.unavailable" class="secondary" :disabled="busy" @click="drain(item)">{{ item.draining ? '恢复领取任务' : '暂停领取新任务' }}</button></div>
        <p v-if="item.unavailable" class="error">服务暂时不可达，无法核验状态。</p><template v-else><p>{{ item.draining ? '已暂停领取' : '正常领取' }} · 待投递事件 {{ item.pending_outbox }} · 隔离事件 {{ item.quarantined_events }}</p><p v-for="worker in item.workers" :key="worker.id" class="small muted">Worker {{ worker.id }} · {{ worker.online ? '在线' : '心跳已过期' }} · {{ worker.last_seen }}</p><p v-if="!item.workers.length" class="muted">尚无近期 Worker 心跳，不能确认在线。</p>
          <div v-for="queue in item.queues" :key="queue.name"><h4>{{ queue.name }}</h4><p>排队 {{ queue.counts.queued }} · 在途 {{ queue.counts.running }} · 失败 {{ queue.counts.failed }} · 过期租约 {{ queue.expired_leases }} · 最长等待 {{ queue.oldest_wait_seconds ?? '—' }} 秒</p><details><summary>最近待处理和失败任务</summary><ul><li v-for="job in queue.recent" :key="job.id"><code>{{ job.id }}</code> · {{ job.status }} · 第 {{ job.attempt ?? 0 }} 次尝试</li></ul></details></div>
        </template></article>
    </section>
  </section>
</template>
