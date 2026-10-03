<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
import RunDiagnostics from './RunDiagnostics.vue'
import ResourceDrawer from './ResourceDrawer.vue'
const picked=ref<string[]>([])
const compared=computed(()=>items.value.filter(item=>picked.value.includes(item.run_id)))
const detailOpen=computed({get:()=>!!selected.value,set:(open:boolean)=>{if(!open)selected.value=''}})
const different=computed(()=>new Set(compared.value.map(item=>item.question)).size>1)

const selected = ref('')
const items = ref<any[]>([]), error = ref(''), loading = ref(false)
async function refresh() {
  loading.value = true
  try { items.value = (await api('/admin/v1/runs/comparison')).items; error.value = '' }
  catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
const states: Record<string, string> = { queued: '排队中', running: '进行中', succeeded: '完成', partial: '部分完成', failed: '未完成', cancelled: '已停止', waiting_input: '等待补充' }
onMounted(refresh)
</script>
<template>
  <section class="resource-panel"><div class="page-title"><h1>运行对照</h1><button class="secondary" :disabled="loading" @click="refresh">刷新</button></div><p class="muted">本账号最近 12 次智能调查。选择 2 至 3 条记录并排查看。比较效果时应使用相同问题、资料范围和配置；只查看记录不会调用模型。</p><p v-if="error" class="error">{{ error }}</p><div class="panel-card"><h3>已选 {{picked.length}} / 3 条运行</h3><p v-if="compared.length<2" class="muted">从下方列表勾选至少两条运行。</p><template v-else><p v-if="different" class="notice">所选运行的问题不同，耗时与用量不能直接用于判断效果优劣。</p><div class="compare-grid"><article v-for="item in compared" :key="item.run_id" class="resource-card"><span class="status-pill">{{item.strategy==='multi_agent'?'多 Agent':'单 Agent'}} · {{states[item.status] || item.status}}</span><h3>{{item.question}}</h3><dl><dt>耗时</dt><dd>{{item.elapsed_ms==null?'未取得':(item.elapsed_ms/1000).toFixed(1)+' 秒'}}</dd><dt>模型 / 工具调用</dt><dd>{{item.budget?.model_calls ?? '—'}} / {{item.budget?.tools ?? '—'}}</dd><dt>已核验 Token</dt><dd>{{item.budget?.settled_tokens?.toLocaleString() ?? '—'}}</dd><dt>专业 Agent 分支</dt><dd>{{item.professional_count}}</dd></dl><button class="secondary" @click="selected=item.run_id">查看运行详情</button></article></div><p class="small muted">资料范围和配置版本请在运行详情中核对。</p></template></div><p v-if="loading" class="loading-state">正在加载运行记录…</p><div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>选择</th><th>问题</th><th>策略 / 状态</th><th>耗时</th><th>模型 / 工具</th><th>已核验 Token</th><th>分支数</th></tr></thead><tbody><tr v-for="item in items" :key="item.run_id"><td><input type="checkbox" v-model="picked" :value="item.run_id" :disabled="picked.length>=3 && !picked.includes(item.run_id)" :aria-label="'对照：'+item.question"/></td><td><button class="text-button" @click="selected = item.run_id">{{ item.question }}</button></td><td>{{ item.strategy === 'multi_agent' ? '多 Agent' : '单 Agent' }} · {{ states[item.status] || item.status }}</td><td>{{ item.elapsed_ms == null ? '—' : (item.elapsed_ms / 1000).toFixed(1) + ' 秒' }}</td><td>{{ item.budget?.model_calls ?? '—' }} / {{ item.budget?.tools ?? '—' }}</td><td>{{ item.budget?.settled_tokens ?? '—' }}<span v-if="item.budget?.unreconciled_calls">（部分用量待对账）</span></td><td>{{ item.professional_count }}</td></tr></tbody></table></div><p v-if="!loading && !items.length" class="muted">暂无可访问的运行记录</p><p class="small muted">金额未配置；不将未知金额显示为零。Token 包含请求输入与输出，不等同于费用。</p><ResourceDrawer v-model="detailOpen" title="运行详情" width="1050px"><RunDiagnostics v-if="selected" :run-id="selected" /></ResourceDrawer></section>
</template>

<style scoped>.compare-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}dl{display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:13px}dt{color:var(--muted)}dd{margin:0;text-align:right}</style>
