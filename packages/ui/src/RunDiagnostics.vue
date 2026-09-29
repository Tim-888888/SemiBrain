<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api } from './api'
const props = defineProps<{ runId: string }>()
const data = ref<any>(null), error = ref(''), loading = ref(false)
let generation = 0
async function refresh() {
  const current = ++generation; loading.value = true; data.value = null; error.value = ''
  try { const result = await api(`/admin/v1/runs/${props.runId}/diagnostics`); if (current === generation) data.value = result }
  catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { if (current === generation) loading.value = false }
}
const labels: Record<string, string> = { reserved: '已预留，等待结算', completed: '已完成', succeeded: '成功', partial: '部分完成', failed: '失败', unknown: '状态或用量待核验', cancelled: '已取消', usage_unknown: '用量未知' }
watch(() => props.runId, refresh)
onMounted(refresh)
</script>
<template>
  <section class="run-diagnostics">
    <div class="page-title"><h2>运行详情</h2><button class="secondary" :disabled="loading" @click="refresh">刷新</button></div>
    <p class="small muted">运行编号：{{ runId }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="loading" role="status">正在读取执行记录…</p>
    <template v-if="data">
      <p v-if="data.configuration_version" class="small muted">任务固定配置版本：{{ data.configuration_version }}</p>
      <p>模型请求 {{ data.counts.model }} 次 · 工具调用 {{ data.counts.tool }} 次<span v-if="data.stop_code"> · 结束原因：{{ data.stop_code }}</span></p>
      <p v-if="data.trace.url"><a :href="data.trace.url" target="_blank" rel="noopener noreferrer">在 Langfuse 查看链路 ↗</a><span class="small muted">（需登录有权访问的 Langfuse 项目；遥测可能延迟）</span></p>
      <p v-else class="small muted">Langfuse 跳转尚未配置。下方显示实际持久化记录，独立于遥测服务。</p>
      <div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>阶段 / 工具</th><th>状态</th><th>模型 / 版本</th><th>耗时</th><th>输入 / 输出 / 总 Token</th></tr></thead><tbody>
        <tr v-for="item in data.items" :key="item.kind + item.id"><td>{{ item.phase || item.tool || item.kind }}<small v-if="item.task_id" class="muted"> · {{ item.task_id }}</small></td><td>{{ labels[item.status] || item.status || '未知' }}<span v-if="item.error_code"> · {{ item.error_code }}</span></td><td>{{ item.profile.model || '—' }}<small v-if="item.profile.version"> / {{ item.profile.version }}</small></td><td>{{ item.elapsed_ms == null ? '—' : (item.elapsed_ms / 1000).toFixed(1) + ' 秒' }}</td><td>{{ item.usage.input_tokens ?? '—' }} / {{ item.usage.output_tokens ?? '—' }} / {{ item.usage.total_tokens ?? '待核验' }}</td></tr>
      </tbody></table></div>
      <p v-if="!data.items.length" class="muted">尚无调用记录。</p><p v-if="data.truncated" class="muted">每类最多显示前 300 条；总次数按全部记录统计。</p>
      <p class="small muted">预留额度不计为实际用量；未配置价格的金额保持未知。此处不展示密钥、工具正文或隐藏推理。</p>
    </template>
  </section>
</template>
