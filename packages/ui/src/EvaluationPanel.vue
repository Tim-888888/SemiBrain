<script setup lang="ts">
import { rememberFilters } from './page-filters'
import { computed, onMounted, ref } from 'vue'
import { api, post } from './api'
import RunDiagnostics from './RunDiagnostics.vue'
import ResourceDrawer from './ResourceDrawer.vue'
const mode=ref(''), state=ref(''), query=ref(''), since=ref('')
const detailOpen=ref(false)
const modes:Record<string,string>={multi_agent:'多 Agent',single_agent:'单 Agent',quick_qa:'快速问答',quick:'快速问答'}
const outcomes:Record<string,string>={complete:'完整完成',partial:'部分完成',failed:'未完成'}
const filtered=computed(()=>(data.value?.items || []).filter((row:any)=>(!mode.value || row.strategy===mode.value) && (!state.value || row.status===state.value) && (!since.value || new Date(row.created_at).getTime()>=new Date(since.value).getTime()) && `${row.question || ''} ${row.run_id}`.toLowerCase().includes(query.value.toLowerCase())))

const data = ref<any>(null), selected = ref<any>(null), busy = ref(false), error = ref(''), notice = ref('')
const form = ref({ outcome: 'complete', grounding: 'not_checked', expected_action: '', note: '' })
const actions = ['greeting','knowledge','attachment','explain','rewrite','business','clarify','investigate']
const states: Record<string,string> = { succeeded: '完成', partial: '部分完成', failed: '未完成', cancelled: '已取消', running: '进行中', queued: '排队中' }
const percent = (value: number | null) => value == null ? '尚未标注' : (value * 100).toFixed(1) + '%'
async function load() { data.value = await api('/admin/v1/evaluations') }
async function refresh() { busy.value = true; error.value = ''; try { await load() } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
function choose(row: any) { selected.value = row; const old = row.evaluation || {}; form.value = { outcome: old.outcome || 'complete', grounding: old.grounding || 'not_checked', expected_action: old.expected_action || '', note: old.note || '' }; notice.value = ''; detailOpen.value = true }
async function save() { busy.value = true; error.value = ''; try { await post('/admin/v1/evaluations', { request_id: crypto.randomUUID(), run_id: selected.value.run_id, expected_revision: selected.value.evaluation?.revision || 0, ...form.value, expected_action: form.value.expected_action || null }); const id = selected.value.run_id; await load(); choose(data.value.items.find((item: any) => item.run_id === id)); detailOpen.value=false; notice.value = '人工验收记录已保存。' } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
onMounted(refresh)
rememberFilters('EvaluationPanel', {mode,state,query,since})
</script>
<template>
  <section class="resource-panel"><div class="page-title"><h1>评测与用量</h1><button class="secondary" :disabled="busy" @click="refresh">刷新</button></div>
    <p class="muted">本账号最近 50 次任务。运行状态来自持久记录；质量与引用支持由人工核验后标注。此样本不是独立测试集，不能代表生产准确率。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="busy && !data" class="loading-state" role="status">正在加载…</p><template v-if="data"><div class="metric-grid"><div class="metric-card"><span>本账号近期任务</span><strong>{{data.summary.run_count}}</strong><small>最近最多 50 次运行</small></div><div class="metric-card"><span>人工验收标注</span><strong>{{data.summary.human_label_count}}</strong><small>执行完成不等于回答正确</small></div><div class="metric-card"><span>已核验引用支持率</span><strong>{{percent(data.summary.grounding_supported_rate)}}</strong><small>已核验 {{data.summary.grounding_checked_count}} 条 · 注意样本量</small></div><div class="metric-card"><span>完成任务耗时 P95</span><strong>{{data.summary.p95_elapsed_ms==null?'暂无':(data.summary.p95_elapsed_ms/1000).toFixed(1)+'s'}}</strong><small>{{data.summary.elapsed_sample_count}} 条耗时样本</small></div></div><p class="small muted">路由动作宏 F1 {{percent(data.summary.action_macro_f1)}} · {{data.summary.action_label_count}} 条人工标注</p>
      <p class="small muted">金额：未配置价格。输入、输出和缓存 Token 按模型回执统计，缺失用量单列，不按零计算。不同任务与策略的耗时需结合任务内容比较。</p>
      <div class="page-toolbar"><input v-model="query" type="search" placeholder="搜索已有问题字段或运行编号" aria-label="搜索运行"/><select v-model="mode" aria-label="运行模式"><option value="">全部模式</option><option v-for="(name,key) in modes" :value="key">{{name}}</option></select><select v-model="state" aria-label="运行状态"><option value="">全部状态</option><option v-for="(name,key) in states" :value="key">{{name}}</option></select><input v-model="since" type="date" aria-label="开始日期"/><span class="result-count">{{filtered.length}} 条记录</span></div><div v-if="!filtered.length" class="empty-card">没有匹配的运行记录。</div>
      <div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>运行</th><th>模式 / 状态</th><th>输入 / 输出 Token</th><th>缓存 Token / 未核验请求</th><th>人工质量</th></tr></thead><tbody><tr v-for="row in filtered" :key="row.run_id"><td><button class="text-button" @click="choose(row)">{{ row.question || row.goal || row.run_id.slice(0,8) }}</button><small>{{ row.created_at }}</small></td><td>{{ modes[row.strategy] || row.strategy }} · {{ states[row.status] || row.status }}</td><td>{{ row.model_usage?.input_tokens ?? '—' }} / {{ row.model_usage?.output_tokens ?? '—' }}</td><td>{{ row.model_usage?.cached_input_tokens ?? '—' }} / {{ row.model_usage?.unknown_usage ?? '—' }}<small v-if="row.model_usage?.unknown_cache">{{ row.model_usage.unknown_cache }} 次缓存量未报告</small></td><td>{{ outcomes[row.evaluation?.outcome] || '尚未标注' }}</td></tr></tbody></table></div>
      <p v-if="data.calls_truncated" class="muted">调用统计达到上限，只含前 10000 条回执。</p>
      <ResourceDrawer v-model="detailOpen" title="运行详情与人工验收" width="1060px" :busy="busy" :snapshot="form" :error="error"><article v-if="selected"><h2>人工验收 {{ selected.run_id.slice(0,8) }}</h2><p class="small muted">请先查看并核对工作台中的原回答与引用，再保存标注。界面不能自动确认事实正确。</p><form @submit.prevent="save"><fieldset :disabled="busy || !['succeeded','partial','failed','cancelled'].includes(selected.status)"><label>目标完成程度<select v-model="form.outcome"><option value="complete">完整完成</option><option value="partial">部分完成</option><option value="failed">未完成</option></select></label><label>引用核验<select v-model="form.grounding"><option value="not_checked">未核验／不适用</option><option value="supported">已核验，来源支持</option><option value="unsupported">存在不支持的结论</option></select></label><label>期望路由动作<select v-model="form.expected_action"><option value="">不参与路由评分</option><option v-for="action in actions" :key="action">{{ action }}</option></select></label><p class="small muted">实际动作：{{ selected.predicted_action || '未取得' }}</p><label>简要说明<textarea v-model="form.note" rows="3" maxlength="500" /></label><button class="primary">保存人工验收</button></fieldset></form><RunDiagnostics :run-id="selected.run_id" /></article></ResourceDrawer>
    </template>
  </section>
</template>
<style scoped>
small{display:block;color:var(--muted,#718779);margin-top:5px}fieldset{border:1px solid var(--border,#dce6df);border-radius:10px;padding:18px}label{display:grid;gap:7px;margin-bottom:14px}select,textarea{padding:8px;border:1px solid var(--border,#dce6df);border-radius:6px;color:inherit;background:white}article{margin-top:25px}
</style>
