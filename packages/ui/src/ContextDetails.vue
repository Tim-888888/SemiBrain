<script setup lang="ts">
import type { ContextUsage } from './api'
defineProps<{ value: ContextUsage }>()
const labels: Record<string, string> = { system: '系统规则', tools: '工具定义', summary: '历史摘要', history: '近期对话', evidence: '工具结果与证据', current: '当前任务与工作消息', overhead: '消息封装' }
const roles: Record<string, string> = { understanding: '问题理解', investigator: '调查与回答', supervisor: '协调器', rag: '知识检索', sqlbot: '业务数据', tool: '计算与外部资料', vision: '图片观察', rca: '汇总回答', reviewer: '答案核验' }
const count = (value?: number | null) => typeof value === 'number' ? value.toLocaleString('zh-CN') : '未返回'
</script>
<template>
  <div class="context-usage">
    <p class="small muted">最近一次请求上下文：约 {{ count(value.latest.estimated_input_tokens) }} / {{ count(value.latest.context_window_tokens) }} Token；本轮已整理历史 {{ value.compaction_count }} 次<span v-if="value.reused_summary_count">，引用 {{ value.reused_summary_count }} 份历史摘要</span>。</p>
    <details>
      <summary>上下文详情</summary>
      <p class="small muted">以下为各角色最近一次请求，窗口独立计算。组成是发送前估算，供应商实际用量单独列出；历史整理不会删除原始消息。</p>
      <section v-for="item in value.agents" :key="item.request_id" class="context-request">
        <strong>{{ roles[item.role] || item.role }} · {{ item.model }}</strong>
        <p class="small muted">{{ item.phase === 'context.compact' ? '历史整理' : '模型请求' }} · {{ new Date(item.created_at).toLocaleString() }} · {{ item.status === 'running' ? '执行中' : item.status === 'completed' ? '已完成' : '用量待核验' }}</p>
        <dl>
          <template v-for="(amount, key) in item.breakdown" :key="key"><dt>{{ labels[key] || key }}</dt><dd>约 {{ count(amount) }}</dd></template>
          <dt>输入总计（估算）</dt><dd>{{ count(item.estimated_input_tokens) }}</dd>
          <dt>历史整理阈值</dt><dd>{{ count(item.threshold_tokens) }}</dd>
          <dt>输出预留 / 安全余量</dt><dd>{{ count(item.output_reserve_tokens) }} / {{ count(item.headroom_tokens) }}</dd>
          <dt>实际输入 / 输出</dt><dd>{{ count(item.usage?.input_tokens) }} / {{ count(item.usage?.output_tokens) }}</dd>
          <dt>实际缓存命中输入</dt><dd>{{ count(item.usage?.cached_input_tokens) }}</dd>
        </dl>
      </section>
      <p class="small muted">已记录 {{ value.request_count }} 次模型请求；已核验输入 {{ count(value.usage.input_tokens) }}、输出 {{ count(value.usage.output_tokens) }} Token<span v-if="value.cache_reported_requests">，{{ value.cache_reported_requests }} 次返回了缓存用量，共命中 {{ count(value.usage.cached_input_tokens) }} Token</span><span v-else>；缓存用量未返回</span><span v-if="value.unknown_usage_requests">；{{ value.unknown_usage_requests }} 次用量待核验</span>。这里仅统计 Agent 直接模型请求；已登记的搜索、读页等外部工具用量计入上方执行累计。累计用量不代表单次上下文占用。</p>
    </details>
  </div>
</template>
<style scoped>
.context-usage { margin: 12px 0; }
.context-request { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border, #dce6df); }
dl { display: grid; grid-template-columns: minmax(120px, 1fr) minmax(90px, 1fr); gap: 6px 16px; font-size: .86rem; }
dt, dd { margin: 0; }
dd { text-align: right; font-variant-numeric: tabular-nums; }
summary { cursor: pointer; }
</style>
