<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post } from './api'
import RunDiagnostics from './RunDiagnostics.vue'
const data = ref<any>(null), selected = ref<any>(null), busy = ref(false), error = ref(''), notice = ref('')
const form = ref({ outcome: 'complete', grounding: 'not_checked', expected_action: '', note: '' })
const actions = ['greeting','knowledge','attachment','explain','rewrite','business','clarify','investigate']
const states: Record<string,string> = { succeeded: '完成', partial: '部分完成', failed: '未完成', cancelled: '已取消', running: '进行中', queued: '排队中' }
const percent = (value: number | null) => value == null ? '尚未标注' : (value * 100).toFixed(1) + '%'
async function load() { data.value = await api('/admin/v1/evaluations') }
async function refresh() { busy.value = true; error.value = ''; try { await load() } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
function choose(row: any) { selected.value = row; const old = row.evaluation || {}; form.value = { outcome: old.outcome || 'complete', grounding: old.grounding || 'not_checked', expected_action: old.expected_action || '', note: old.note || '' }; notice.value = '' }
async function save() { busy.value = true; error.value = ''; try { await post('/admin/v1/evaluations', { request_id: crypto.randomUUID(), run_id: selected.value.run_id, expected_revision: selected.value.evaluation?.revision || 0, ...form.value, expected_action: form.value.expected_action || null }); const id = selected.value.run_id; await load(); choose(data.value.items.find((item: any) => item.run_id === id)); notice.value = '人工验收记录已保存。' } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
onMounted(refresh)
</script>
<template>
  <section class="resource-panel"><div class="page-title"><h1>评测与用量</h1><button class="secondary" :disabled="busy" @click="refresh">刷新</button></div>
    <p class="muted">本账号最近 50 次任务。运行状态来自持久记录；质量与引用支持由人工核验后标注。此样本不是独立测试集，不能代表生产准确率。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="data"><p>任务 {{ data.summary.run_count }} · 人工标注 {{ data.summary.human_label_count }} · 已核验引用支持率 {{ percent(data.summary.grounding_supported_rate) }}（{{ data.summary.grounding_checked_count }} 条）</p>
      <p>路由动作宏 F1 {{ percent(data.summary.action_macro_f1) }}（{{ data.summary.action_label_count }} 条标注） · 完成任务耗时 p95 {{ data.summary.p95_elapsed_ms == null ? '暂无' : (data.summary.p95_elapsed_ms / 1000).toFixed(1) + ' 秒' }}（{{ data.summary.elapsed_sample_count }} 条）</p>
      <p class="small muted">金额：未配置价格。输入、输出和缓存 Token 按模型回执统计，缺失用量单列，不按零计算。不同任务与策略的耗时需结合任务内容比较。</p>
      <div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>运行</th><th>模式 / 状态</th><th>输入 / 输出 Token</th><th>缓存 Token / 未核验请求</th><th>人工质量</th></tr></thead><tbody><tr v-for="row in data.items" :key="row.run_id"><td><button class="text-button" @click="choose(row)">{{ row.run_id.slice(0,8) }}</button><small>{{ row.created_at }}</small></td><td>{{ row.strategy }} · {{ states[row.status] || row.status }}</td><td>{{ row.model_usage?.input_tokens ?? '—' }} / {{ row.model_usage?.output_tokens ?? '—' }}</td><td>{{ row.model_usage?.cached_input_tokens ?? '—' }} / {{ row.model_usage?.unknown_usage ?? '—' }}<small v-if="row.model_usage?.unknown_cache">{{ row.model_usage.unknown_cache }} 次缓存量未报告</small></td><td>{{ row.evaluation?.outcome || '尚未标注' }}</td></tr></tbody></table></div>
      <p v-if="data.calls_truncated" class="muted">调用统计达到上限，只含前 10000 条回执。</p>
      <article v-if="selected"><h2>人工验收 {{ selected.run_id.slice(0,8) }}</h2><p class="small muted">请先查看并核对工作台中的原回答与引用，再保存标注。界面不能自动确认事实正确。</p><form @submit.prevent="save"><fieldset :disabled="busy || !['succeeded','partial','failed','cancelled'].includes(selected.status)"><label>目标完成程度<select v-model="form.outcome"><option value="complete">完整完成</option><option value="partial">部分完成</option><option value="failed">未完成</option></select></label><label>引用核验<select v-model="form.grounding"><option value="not_checked">未核验／不适用</option><option value="supported">已核验，来源支持</option><option value="unsupported">存在不支持的结论</option></select></label><label>期望路由动作<select v-model="form.expected_action"><option value="">不参与路由评分</option><option v-for="action in actions" :key="action">{{ action }}</option></select></label><p class="small muted">实际动作：{{ selected.predicted_action || '未取得' }}</p><label>简要说明<textarea v-model="form.note" rows="3" maxlength="500" /></label><button class="primary">保存人工验收</button></fieldset></form><RunDiagnostics :run-id="selected.run_id" /></article>
    </template>
  </section>
</template>
<style scoped>
small{display:block;color:var(--muted,#718779);margin-top:5px}fieldset{border:1px solid var(--border,#dce6df);border-radius:10px;padding:18px}label{display:grid;gap:7px;margin-bottom:14px}select,textarea{padding:8px;border:1px solid var(--border,#dce6df);border-radius:6px;color:inherit;background:white}article{margin-top:25px}
</style>
