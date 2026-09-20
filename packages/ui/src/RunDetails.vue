<script setup lang="ts">
import type { Run } from './api'
defineProps<{ run: Partial<Run> }>()
</script>
<template>
  <details v-if="run.strategy === 'single_agent'" class="run-details">
    <summary>智能调查<span v-if="run.round"> · {{ run.round }} 轮</span><span v-if="run.model_origin === 'api_simulated'"> · API 模型演示</span></summary>
    <div v-if="run.scope_summary" class="scope-summary">
      <p v-if="run.scope_summary.goals?.length"><strong>本次目标</strong></p>
      <ul><li v-for="goal in run.scope_summary.goals" :key="goal">{{ goal }}</li></ul>
      <p v-if="run.scope_summary.constraints?.length"><strong>范围与限制</strong></p>
      <ul><li v-for="constraint in run.scope_summary.constraints" :key="constraint">{{ constraint }}</li></ul>
      <p>{{ run.scope_summary.allow_web && !run.web_disabled ? '本轮允许搜索公开网络资料' : '本轮联网关闭' }}</p>
    </div>
    <p v-if="run.budget" class="small muted">已发起 {{ run.budget.model_calls }} 次模型请求、{{ run.budget.tools }} 次工具调用；已核验用量 {{ run.budget.settled_tokens }} Token<span v-if="run.budget.unreconciled_calls">，{{ run.budget.unreconciled_calls }} 次请求用量待对账</span>。</p>
    <p v-if="run.continuation" class="small muted">{{ run.continuation.kind === 'clarification' ? '本次补充了上一轮缺少的信息' : '范围已改变，已重新启动调查' }}</p>
    <p v-if="run.status === 'partial'" class="small muted">仅完成部分目标，请以回答中说明的证据和限制为准。</p>
    <p v-if="run.status === 'waiting_input'" class="small muted">补充信息后可继续提问；改变资料或范围将创建新的调查。</p>
  </details>
</template>
