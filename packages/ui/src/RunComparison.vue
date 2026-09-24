<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from './api'
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
  <section class="resource-panel"><div class="page-title"><h1>运行对照</h1><button class="secondary" :disabled="loading" @click="refresh">刷新</button></div><p class="muted">本账号最近 12 次智能调查。比较效果时需使用相同问题、资料范围和预算；下表不将不同任务直接评为优劣。</p><p v-if="error" class="error">{{ error }}</p><div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>问题</th><th>策略 / 状态</th><th>耗时</th><th>模型 / 工具</th><th>已核验 Token</th><th>分支数</th></tr></thead><tbody><tr v-for="item in items" :key="item.run_id"><td>{{ item.question }}</td><td>{{ item.strategy === 'multi_agent' ? '多 Agent' : '单 Agent' }} · {{ states[item.status] || item.status }}</td><td>{{ item.elapsed_ms == null ? '—' : (item.elapsed_ms / 1000).toFixed(1) + ' 秒' }}</td><td>{{ item.budget?.model_calls ?? '—' }} / {{ item.budget?.tools ?? '—' }}</td><td>{{ item.budget?.settled_tokens ?? '—' }}<span v-if="item.budget?.unreconciled_calls">（部分用量待对账）</span></td><td>{{ item.professional_count }}</td></tr></tbody></table></div><p v-if="!loading && !items.length" class="muted">暂无可访问的运行记录</p><p class="small muted">金额未配置；不将未知金额显示为零。Token 包含请求输入与输出，不等同于费用。</p></section>
</template>
