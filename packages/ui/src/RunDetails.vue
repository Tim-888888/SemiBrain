<script setup lang="ts">
import type { Run } from './api'
defineProps<{ run: Partial<Run> }>()
const webLabels: Record<string, string> = { pending: '等待问题理解', running: '搜索中', succeeded: '已完成', partial: '部分完成', failed: '未取得结果', cancelled: '已停止', disabled: '已关闭', skipped: '本轮无需搜索' }
const taskLabels: Record<string, string> = { queued: '等待依赖', running: '进行中', succeeded: '完成', partial: '部分完成', failed: '未完成', cancelled: '已停止' }
const roles: Record<string, string> = { sqlbot: '业务数据', rag: '知识检索', vision: '图片观察', tool: '计算与外部资料' }
function searchLabel(run: Partial<Run>) {
  if (run.web_disabled) return '已关闭'
  if (run.status === 'cancelling') return '正在停止'
  if (run.status === 'cancelled') return '已停止'
  return webLabels[run.web_activity?.search || 'pending'] || '等待完成'
}
</script>
<template>
  <details v-if="['single_agent', 'multi_agent', 'quick_web'].includes(run.strategy || '')" class="run-details">
    <summary>{{ run.strategy === 'quick_web' ? '快速问答 · 联网资料' : run.strategy === 'multi_agent' ? '智能调查 · 多 Agent 协作' : '智能调查 · 单 Agent' }}<span v-if="run.round"> · {{ run.round }} 轮</span><span v-if="run.model_origin === 'remote_api'"> · API 模型</span><span v-else-if="run.model_origin === 'api_simulated'"> · API 角色演示</span></summary>
    <ol v-if="run.task_tree?.length" class="task-tree" aria-label="专业 Agent 任务树">
      <li v-for="task in run.task_tree" :key="task.task_id"><strong>{{ roles[task.role] || task.role }}</strong> · {{ taskLabels[task.status] || task.status }}<p>{{ task.goals.join('；') }}</p><small v-if="task.depends_on.length">前置任务：{{ task.depends_on.join('、') }} · </small><small>计划 {{ task.plan_version }} · 尝试 {{ task.attempt }} · {{ task.model_calls || 0 }} 次模型 / {{ task.tool_calls || 0 }} 次工具 · {{ task.settled_tokens || 0 }} Token</small><p v-if="task.error" class="small muted">分支未完成：{{ task.error }}</p></li>
    </ol>
    <div v-if="run.scope_summary" class="scope-summary">
      <p v-if="run.scope_summary.goals?.length"><strong>本次目标</strong></p>
      <ul><li v-for="goal in run.scope_summary.goals" :key="goal">{{ goal }}</li></ul>
      <p v-if="run.scope_summary.constraints?.length"><strong>范围与限制</strong></p>
      <ul><li v-for="constraint in run.scope_summary.constraints" :key="constraint">{{ constraint }}</li></ul>
      <p>{{ run.scope_summary.allow_web && !run.web_disabled ? '本轮允许搜索公开网络资料' : '本轮联网关闭' }}</p>
    </div>
    <p v-if="run.budget" class="small muted">已发起 {{ run.budget.model_calls }} 次模型请求、{{ run.budget.tools }} 次工具调用；已核验用量 {{ run.budget.settled_tokens }} Token<span v-if="run.budget.unreconciled_calls">，{{ run.budget.unreconciled_calls }} 次请求用量待对账</span>。</p>
    <template v-if="run.web_activity">
      <p class="small muted">{{ run.web_activity.reason }} · 网络搜索：{{ searchLabel(run) }}<span v-if="run.web_activity.results !== undefined">（{{ run.web_activity.results }} 个候选来源）</span>。</p>
      <p v-if="run.web_activity.pages.length" class="small muted">已完成 {{ run.web_activity.pages.length }} 次网页读取尝试，其中 {{ run.web_activity.pages.filter(page => ['succeeded', 'partial'].includes(page.status)).length }} 次取得正文。搜索网址和未读正文不作为回答证据。</p>
    </template>
    <p v-else-if="run.budget" class="small muted">实际网络搜索 {{ run.budget.searches ?? 0 }} 次，网页抓取 {{ run.budget.pages ?? 0 }} 次（调用次数不代表已成功取得正文）。</p>
    <p v-if="run.continuation" class="small muted">{{ run.continuation.kind === 'clarification' ? '本次补充了上一轮缺少的信息' : '范围已改变，已重新启动调查' }}</p>
    <p v-if="run.status === 'partial'" class="small muted">仅完成部分目标，请以回答中说明的证据和限制为准。</p>
    <p v-if="run.status === 'waiting_input'" class="small muted">补充信息后可继续提问；改变资料或范围将创建新的调查。</p>
  </details>
</template>
