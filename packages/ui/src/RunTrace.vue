<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from './api'

type Usage = { input_tokens?: number; output_tokens?: number; total_tokens?: number }
type Step = { id: string; kind: string; phase?: string; tool?: string; name?: string; parent_id?: string | null; status?: string; created_at?: string; elapsed_ms?: number | null; usage: Usage; profile?: { model?: string }; error_code?: string }
type Trace = { status: string; counts: { model: number; tool: number }; items: Step[]; truncated: boolean; trace?: { url?: string; sso_url?: string }; telemetry: { status: string; items: Step[]; truncated: boolean } }
const props = defineProps<{ runId: string; isAdmin?: boolean }>()
const data = ref<Trace | null>(null), error = ref(''), loading = ref(false)
const view = ref('calls'), kind = ref('all')
let generation = 0, controller: AbortController | undefined
async function refresh() {
  const current = ++generation
  controller?.abort(); controller = new AbortController()
  loading.value = true; data.value = null; error.value = ''
  try {
    const result = await api<Trace>(`/v1/runs/${props.runId}/trace`, { signal: controller.signal })
    if (current === generation) data.value = result
  } catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { if (current === generation) loading.value = false }
}
watch(() => props.runId, () => { view.value = 'calls'; kind.value = 'all'; refresh() }, { immediate: true })
onUnmounted(() => { generation++; controller?.abort() })
const states: Record<string, string> = { dispatching: '等待受理', queued: '等待执行', running: '执行中', retrying: '重试中', cancelling: '停止中', waiting_input: '等待补充', reserved: '待结算', completed: '已完成', succeeded: '成功', partial: '部分完成', failed: '失败', cancelled: '已取消', unknown: '待核验', usage_unknown: '用量待核验', warning: '需留意' }
const phases: Record<string, string> = { understand: '理解问题', quick_understand: '理解问题', plan: '制定计划', quick_answer: '生成回答', answer: '生成回答', review: '证据审核', synthesize: '汇总回答', 'expert.rag': '知识检索', 'expert.business': '业务查询', 'expert.compute': '计算与外部资料', 'conversation.accepted': '受理任务' }
const notices: Record<string, string> = { pending: '遥测尚未返回，可能仍在同步。可以先看调用记录，稍后刷新。', unavailable: '遥测服务暂时不可用，下方调用记录仍可查看。', disabled: '本次未启用遥测，下方显示平台保存的调用记录。', historical: '本次使用的是历史遥测项目，下方显示平台保存的调用记录。' }
const title = (item: Step) => phases[item.phase || item.tool || item.name || ''] || item.phase || item.tool || item.name || '执行步骤'
const duration = (value: number | null | undefined) => value == null ? '待核验' : value < 1000 ? `${Math.round(value)} 毫秒` : `${(value / 1000).toFixed(2)} 秒`
const time = (value?: string) => value ? new Date(value).toLocaleTimeString('zh-CN', { hour12: false }) : '时间待核验'
const tokenTotal = computed(() => (data.value?.items || []).filter(i => i.kind === 'model').reduce((n, i) => n + (i.usage.total_tokens || 0), 0))
const missingUsage = computed(() => (data.value?.items || []).filter(i => i.kind === 'model' && i.usage.total_tokens == null).length)
const calls = computed(() => (data.value?.items || []).filter(i => kind.value === 'all' || i.kind === kind.value))
const spans = computed(() => {
  const items = data.value?.telemetry.items || [], byId = new Map(items.map(i => [i.id, i]))
  const seen = new Set<string>(), result: { item: Step; depth: number; parent: string }[] = []
  function visit(item: Step, depth: number) {
    if (seen.has(item.id)) return
    seen.add(item.id)
    result.push({ item, depth: Math.min(depth, 3), parent: item.parent_id ? (byId.has(item.parent_id) ? title(byId.get(item.parent_id)!) : '上游节点未返回') : '根节点' })
    items.filter(child => child.parent_id === item.id).forEach(child => visit(child, depth + 1))
  }
  items.filter(i => !i.parent_id || !byId.has(i.parent_id)).forEach(i => visit(i, 0))
  // Display malformed/cyclic upstream records once without recursing forever.
  items.forEach(i => visit(i, 0))
  return result
})
</script>

<template>
  <section class="run-trace" aria-label="运行链路">
    <header class="trace-heading">
      <div><h2>本次运行链路</h2><p class="muted small">查看执行步骤、耗时和用量</p></div>
      <button class="secondary" :disabled="loading" @click="refresh">刷新</button>
    </header>
    <p class="trace-id small muted">运行编号：{{ runId }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted" role="status">正在读取本次链路…</p>
    <template v-if="data">
      <div class="trace-status-row"><span class="trace-status" :data-status="data.status">{{ states[data.status] || data.status }}</span><a v-if="isAdmin && data.trace?.url" :href="data.trace.sso_url || data.trace.url" target="_blank" rel="noopener noreferrer">完整 Langfuse ↗</a></div>
      <p v-if="isAdmin && data.trace?.url" class="small muted">{{ data.trace.sso_url ? '管理员统一登录，将直接打开本次链路。' : '管理员入口，需登录 Langfuse 项目账号。' }}</p>
      <div class="trace-metrics">
        <article><span>模型请求</span><strong>{{ data.counts.model }} <small>次</small></strong></article>
        <article><span>工具调用</span><strong>{{ data.counts.tool }} <small>次</small></strong></article>
        <article><span>已知 Token</span><strong>{{ tokenTotal.toLocaleString() }}</strong></article>
      </div>
      <p class="small muted">用量按下方模型调用记录汇总，不重复计入遥测节点。<template v-if="missingUsage">{{ missingUsage }} 次请求的用量待核验。</template><template v-if="data.truncated">当前记录有截断，用量不代表全部消耗。</template></p>
      <div class="trace-tabs" aria-label="链路视图">
        <button :aria-pressed="view === 'calls'" @click="view = 'calls'">调用记录</button>
        <button :aria-pressed="view === 'spans'" @click="view = 'spans'">链路层级 <span>{{ data.telemetry.items.length }}</span></button>
      </div>
      <p v-if="notices[data.telemetry.status]" class="trace-notice" role="status">{{ notices[data.telemetry.status] }}</p>
      <template v-if="view === 'calls'">
        <div class="trace-filter"><select v-model="kind" aria-label="调用类型"><option value="all">全部调用</option><option value="model">模型请求</option><option value="tool">工具调用</option></select><span class="small muted">{{ calls.length }} 条 · 按开始时间排列</span></div>
        <ol class="trace-timeline">
          <li v-for="item in calls" :key="item.kind + item.id" class="trace-step">
            <div class="trace-step-heading"><strong>{{ title(item) }}</strong><span class="trace-status" :data-status="item.status">{{ states[item.status || 'unknown'] || item.status }}</span></div>
            <p class="trace-step-meta">{{ item.kind === 'model' ? '模型请求' : '工具调用' }}<span v-if="item.profile?.model"> · {{ item.profile.model }}</span> · {{ time(item.created_at) }}</p>
            <div class="trace-step-facts"><span>耗时 <b>{{ duration(item.elapsed_ms) }}</b></span><span v-if="item.kind === 'model'">Token <b>{{ item.usage.total_tokens?.toLocaleString() ?? '待核验' }}</b></span></div>
            <p v-if="item.kind === 'model'" class="small muted">输入 {{ item.usage.input_tokens?.toLocaleString() ?? '—' }} / 输出 {{ item.usage.output_tokens?.toLocaleString() ?? '—' }}</p>
            <p v-if="item.error_code" class="error small">{{ item.error_code }}</p>
          </li>
        </ol>
        <p v-if="!calls.length" class="muted">{{ data.status === 'dispatching' ? '任务已提交，等待执行服务受理。' : '暂无符合条件的调用记录。' }}</p>
        <p v-if="data.truncated" class="small muted">每类最多显示前 300 条；上方调用次数统计全部记录。</p>
      </template>
      <template v-else>
        <p class="small muted">层级来自实际遥测的父子关系；同步延迟可能导致部分上游节点暂缺。</p>
        <ol class="trace-timeline">
          <li v-for="row in spans" :key="row.item.id" class="trace-step trace-span" :style="{ '--depth': row.depth }">
            <div class="trace-step-heading"><strong>{{ title(row.item) }}</strong><span class="trace-status" :data-status="row.item.status">{{ states[row.item.status || 'unknown'] }}</span></div>
            <p class="trace-step-meta">{{ row.parent }} · {{ time(row.item.created_at) }}</p>
            <div class="trace-step-facts"><span>耗时 <b>{{ duration(row.item.elapsed_ms) }}</b></span><span v-if="row.item.kind === 'model'">Token <b>{{ row.item.usage.total_tokens?.toLocaleString() ?? '待核验' }}</b></span></div>
          </li>
        </ol>
        <p v-if="data.telemetry.truncated" class="small muted">最多显示 300 个遥测节点，当前链路有截断。</p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.run-trace{max-width:960px;margin:auto;color:var(--text,#263b45)}
.trace-heading,.trace-status-row,.trace-step-heading,.trace-filter{display:flex;align-items:center;justify-content:space-between;gap:12px}
.trace-heading h2{margin:0;font-size:22px}.trace-heading p{margin:8px 0}.trace-id{overflow-wrap:anywhere;margin-bottom:20px}
.trace-status-row{flex-wrap:wrap}.trace-status-row a{font-size:14px}.trace-status{background:#edf5f2;color:#326658;border-radius:6px;padding:4px 8px;font-size:12px;white-space:nowrap}
.trace-status[data-status=failed]{background:#fff0ed;color:#a63d2d}.trace-status[data-status=partial],.trace-status[data-status=warning]{background:#fff7e8;color:#916020}
.trace-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:22px}
.trace-metrics article{border:1px solid #e3eaf0;border-radius:12px;padding:16px;background:#fff;min-width:0}
.trace-metrics span{color:#70818c;font-size:13px}.trace-metrics strong{display:block;margin-top:12px;font-size:26px;font-weight:600;overflow-wrap:anywhere}.trace-metrics small{font-size:12px;font-weight:400}
.trace-tabs{display:flex;gap:6px;border-bottom:1px solid #e3eaf0;margin:24px 0 16px;padding-bottom:8px}.trace-tabs button{background:transparent;border:0;border-radius:8px;color:#607581;padding:10px 14px;cursor:pointer}.trace-tabs button[aria-pressed=true]{background:#eaf4f0;color:#137e6f;font-weight:600}.trace-tabs span{font-size:12px;margin-left:4px}
.trace-notice{background:#f6f8fa;border:1px solid #e3eaf0;border-radius:10px;padding:12px 14px;font-size:13px;color:#647785}
.trace-filter{margin:16px 0;flex-wrap:wrap}.trace-filter select{min-height:36px}
.trace-timeline{list-style:none;margin:0;padding:0 0 0 12px;border-left:1px solid #dce9e4}
.trace-step{position:relative;border:1px solid #e3eaf0;border-radius:12px;padding:16px;margin:0 0 12px 8px;background:white;overflow-wrap:anywhere}
.trace-step:before{content:'';position:absolute;width:7px;height:7px;border-radius:50%;background:#168577;left:-25px;top:22px;box-shadow:0 0 0 4px #eef6f3}
.trace-step-heading{align-items:flex-start}.trace-step-heading strong{font-size:14px}.trace-step-heading .trace-status{flex-shrink:0}
.trace-step-meta{font-size:12px;color:#71818c;line-height:1.6;margin:8px 0 12px}.trace-step-facts{display:flex;gap:12px 24px;flex-wrap:wrap;font-size:13px;color:#71818c}.trace-step-facts b{font-weight:500;color:#334f55;margin-left:6px}.trace-step .small{margin:8px 0 0}
.trace-span{margin-left:calc(8px + var(--depth) * 18px)}
@media(max-width:600px){.trace-heading h2{font-size:19px}.trace-metrics{gap:6px}.trace-metrics article{padding:12px 8px}.trace-metrics strong{font-size:21px}.trace-metrics span{font-size:12px}.trace-step{padding:12px}.trace-span{margin-left:calc(8px + var(--depth) * 8px)}}
</style>
