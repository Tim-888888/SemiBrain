<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import AuthPanel from './AuthPanel.vue'
import KnowledgePanel from './KnowledgePanel.vue'
import MarkdownAnswer from './MarkdownAnswer.vue'
import RunDetails from './RunDetails.vue'
import UsersPanel from './UsersPanel.vue'
import { api, post, setCsrf, type Message, type Run, type User } from './api'
import './style.css'
const props = defineProps<{ audience: 'admin' | 'user' }>()
const user = ref<User | null>(null), loading = ref(true), view = ref('chat'), error = ref(''), text = ref('')
const conversations = ref<any[]>([]), conversation = ref<any>(null), messages = ref<Message[]>([]), activeRun = ref<Run | null>(null)
const attached = ref<string[]>([])
const mode = ref<'quick_qa' | 'investigation'>('quick_qa'), allowWeb = ref(false)
const cancelling = ref(false), promptPreview = ref('')
const knowledge = ref<any[]>([]), selectedDocuments = ref<string[]>([]), sending = ref(false), scrollArea = ref<HTMLElement | null>(null)
const settings = ref(false), oldPassword = ref(''), newPassword = ref('')
const conversationsCursor = ref<string | null>(null), messagesCursor = ref<number | null>(null)
const sourceMenu = ref<HTMLDetailsElement | null>(null)
let source: EventSource | null = null
let generation = 0
let pending: { conversationId: string; payload: any } | null = null
const adminPage = computed(() => props.audience === 'admin')
const denied = computed(() => adminPage.value && user.value?.role !== 'admin')
const running = computed(() => sending.value || (!!activeRun.value && !['succeeded', 'failed', 'partial', 'cancelled', 'waiting_input'].includes(activeRun.value.status)))
async function loadLists() {
  const current = generation
  const [page, documents] = await Promise.all([api('/v1/conversations'), api('/v1/knowledge/documents')])
  if (current !== generation) return
  conversations.value = page.items; conversationsCursor.value = page.next_cursor
  knowledge.value = documents.items.filter((item: any) => item.active_version)
  const updated = page.items.find((item: any) => item.id === conversation.value?.id)
  if (updated) conversation.value = updated
}
async function signedIn(value: User) { user.value = value; if (adminPage.value) view.value = 'knowledge'; await loadLists() }
function closeStream() { source?.close(); source = null }
async function bottom() { await nextTick(); scrollArea.value?.scrollTo({ top: scrollArea.value.scrollHeight, behavior: 'smooth' }) }
function newChat() { allowWeb.value = false; generation++; closeStream(); conversation.value = null; messages.value = []; activeRun.value = null; error.value = ''; pending = null; selectedDocuments.value = []; attached.value = []; messagesCursor.value = null; view.value = 'chat' }
async function openChat(item: any) {
  const current = ++generation; closeStream(); view.value = 'chat'; error.value = ''; pending = null
  try {
    const result = await api(`/v1/conversations/${item.id}/messages`)
    if (generation !== current) return
    conversation.value = item; messages.value = result.items; messagesCursor.value = result.next_cursor; activeRun.value = null; selectedDocuments.value = []; attached.value = []
    const last = messages.value.at(-1)
    if (last?.role === 'user') subscribe(last.run_id, current)
    await bottom()
  } catch (e) { if (generation === current) error.value = (e as Error).message }
}
function subscribe(runId: string, current = generation) {
  closeStream(); activeRun.value = { run_id: runId, status: 'queued', sequence: 0, body_markdown: '', citations: [], progress: '准备处理' }
  source = new EventSource(`/v1/runs/${runId}/events`, { withCredentials: true })
  source.addEventListener('snapshot', async event => {
    if (current !== generation) return
    const state: Run = JSON.parse((event as MessageEvent).data)
    if (state.run_id !== runId || state.sequence < (activeRun.value?.sequence || 0)) return
    activeRun.value = state; if (state.web_disabled) allowWeb.value = false; await bottom()
    if (current !== generation) return
    if (['succeeded', 'partial', 'failed', 'cancelled', 'waiting_input'].includes(state.status)) {
      closeStream()
      const revision = conversation.value?.revision || 1
      if (!messages.value.some(message => message.role === 'assistant' && message.run_id === runId)) messages.value.push({ ...state, id: runId, role: 'assistant', input_revision: revision })
      activeRun.value = null; if (state.status === 'waiting_input' && state.input_scope) { mode.value = state.input_scope.mode; allowWeb.value = state.input_scope.allow_web && !state.web_disabled; selectedDocuments.value = state.input_scope.resource_restrictions; attached.value = state.input_scope.attachment_refs }; await loadLists()
    }
  })
  source.addEventListener('access.unavailable', () => {
    if (current !== generation) return
    closeStream(); activeRun.value = null; error.value = '登录、来源或服务状态暂时无法核验。请刷新会话重试。'
  })
}
async function send() {
  if (!text.value.trim() || running.value) return
  if (sourceMenu.value) sourceMenu.value.open = false
  sending.value = true; error.value = ''; const current = generation
  try {
    if (!conversation.value) {
      const requestId = crypto.randomUUID()
      const created = await post('/v1/conversations', { request_id: requestId, title: '新会话' }, requestId)
      if (current !== generation) return
      conversation.value = created
    }
    if (!pending || pending.payload.text !== text.value || pending.payload.mode !== mode.value || pending.payload.allow_web !== allowWeb.value || JSON.stringify(pending.payload.resource_restrictions) !== JSON.stringify(selectedDocuments.value) || JSON.stringify(pending.payload.attachment_refs) !== JSON.stringify(attached.value) || pending.conversationId !== conversation.value.id) pending = {
      conversationId: conversation.value.id, payload: { request_id: crypto.randomUUID(), expected_revision: conversation.value.revision,
        text: text.value, mode: mode.value, continuation_of: messages.value.at(-1)?.status === 'waiting_input' ? messages.value.at(-1)?.run_id : null, resource_restrictions: [...selectedDocuments.value], attachment_refs: [...attached.value], allow_web: allowWeb.value },
    }
    const submitted = pending
    const accepted = await post(`/v1/conversations/${submitted.conversationId}/messages`, submitted.payload, submitted.payload.request_id)
    if (current !== generation) { await loadLists(); return }
    conversation.value.revision = accepted.input_revision
    messages.value.push({ id: accepted.turn_id, role: 'user', text: submitted.payload.text, run_id: accepted.run_id, input_revision: accepted.input_revision })
    text.value = ''; attached.value = []; pending = null; subscribe(accepted.run_id); await loadLists(); await bottom()
  } catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { sending.value = false }
}
async function olderChats() { try { const page = await api('/v1/conversations?after=' + conversationsCursor.value); conversations.value.push(...page.items); conversationsCursor.value = page.next_cursor } catch (e) { error.value = (e as Error).message } }
async function olderMessages() { const current = generation; try { const page = await api(`/v1/conversations/${conversation.value.id}/messages?before=${messagesCursor.value}`); if (current !== generation) return; messages.value.unshift(...page.items); messagesCursor.value = page.next_cursor } catch (e) { error.value = (e as Error).message } }
async function logout() { try { await post('/v1/auth/logout', {}); closeStream(); user.value = null; setCsrf(''); newChat() } catch (e) { error.value = (e as Error).message } }
async function changePassword() {
  try { await post('/v1/auth/password', { old_password: oldPassword.value, new_password: newPassword.value }); settings.value = false; closeStream(); user.value = null; setCsrf(''); oldPassword.value = ''; newPassword.value = '' }
  catch (e) { error.value = (e as Error).message }
}
async function cancelRun() {
  if (!activeRun.value || cancelling.value) return
  cancelling.value = true
  try { const id = crypto.randomUUID(); await post(`/v1/runs/${activeRun.value.run_id}/cancel`, { request_id: id }, id) }
  catch (e) { error.value = (e as Error).message }
  finally { cancelling.value = false }
}
async function disableWeb() {
  if (!activeRun.value) return
  try { const id = crypto.randomUUID(); await post(`/v1/runs/${activeRun.value.run_id}/disable-web`, { request_id: id }, id); allowWeb.value = false }
  catch (e) { error.value = (e as Error).message }
}
async function showPrompt(runId: string) {
  try { promptPreview.value = JSON.stringify(await api(`/admin/v1/runs/${runId}/prompt-preview`), null, 2) }
  catch (e) { error.value = (e as Error).message }
}
function keydown(event: KeyboardEvent) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send() } }
onMounted(async () => { try { const result = await api('/v1/auth/me'); setCsrf(result.csrf); await signedIn(result.user) } catch { user.value = null } finally { loading.value = false } })
onUnmounted(closeStream)
</script>
<template>
  <div v-if="loading" class="loading-page">正在打开 SemiBrain…</div>
  <AuthPanel v-else-if="!user" @signed-in="signedIn" />
  <div v-else-if="denied" class="loading-page"><h1>此账号没有管理权限</h1><p><a href="/">返回工作台</a></p></div>
  <div v-else class="workspace">
    <aside class="sidebar"><a class="wordmark" href="/"><span class="brand-icon">S</span> SemiBrain</a><span v-if="adminPage" class="admin-caption">管理控制台</span>
      <button v-if="!adminPage" class="new-chat" @click="newChat">＋ 新会话</button>
      <nav><button v-if="!adminPage" :class="{ active: view === 'chat' }" @click="view = 'chat'">◈ 对话工作台</button><button :class="{ active: view === 'knowledge' }" @click="view = 'knowledge'">▤ 知识库</button><button v-if="adminPage" :class="{ active: view === 'users' }" @click="view = 'users'">♙ 用户管理</button></nav>
      <div v-if="!adminPage" class="history"><p class="eyebrow">最近会话</p><button v-for="item in conversations" :key="item.id" :class="{ selected: conversation?.id === item.id }" @click="openChat(item)">{{ item.title }}</button><button v-if="conversationsCursor" @click="olderChats">加载更早会话</button><p v-if="!conversations.length" class="small muted">你的会话会保存在这里</p></div>
      <div class="sidebar-bottom"><a v-if="user.role === 'admin' && !adminPage" class="admin-link" href="/admin/">管理控制台 ↗</a><a v-if="adminPage" class="admin-link" href="/">返回工作台 ↗</a><div class="profile"><span class="avatar">{{ user.username.slice(0, 1).toUpperCase() }}</span><div><strong>{{ user.username }}</strong><small>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</small></div><button class="text-button" @click="settings = true" aria-label="账号设置">⚙</button></div><button class="text-button logout" @click="logout">退出登录</button></div>
    </aside>
    <main class="main-panel"><header class="topbar"><span>{{ view === 'chat' ? conversation?.title || '对话工作台' : view === 'knowledge' ? '知识库' : '用户管理' }}</span><span class="workspace-tag">半导体知识空间</span></header>
      <KnowledgePanel v-if="view === 'knowledge'" :manage="adminPage && user.role === 'admin'" />
      <UsersPanel v-else-if="view === 'users'" />
      <template v-else>
        <div ref="scrollArea" class="conversation-scroll">
          <div v-if="!messages.length && !activeRun" class="welcome"><span class="welcome-symbol">✳</span><p class="eyebrow">SEMI BRAIN / KNOWLEDGE ASSISTANT</p><h1>从一个问题开始</h1><p>查阅资料，理解工艺，核验每一条来源。</p><div class="starter-grid"><button @click="text = '知识库中有哪些关于测试良率的资料？'">▤ 查找专业资料<span>从已发布文档中寻找证据 ↗</span></button><button @click="text = '请列出可查询的合成演示批次。'">▦ 查询演示数据<span>了解批次与测试上下文 ↗</span></button></div></div>
          <div class="message-column"><button v-if="messagesCursor" class="text-button" @click="olderMessages">加载更早消息</button><section v-for="message in messages" :key="message.id" :class="['message', message.role]"><div v-if="message.role === 'user'" class="question-bubble">{{ message.text }}</div><template v-else><div class="assistant-label"><span class="mini-brand">S</span> SemiBrain<span v-if="message.status === 'failed'" class="muted">处理未完成</span></div><RunDetails :run="message" /><button v-if="user.role === 'admin' && message.strategy === 'single_agent'" class="text-button" @click="showPrompt(message.run_id)">查看脱敏提示词</button><MarkdownAnswer :text="message.body_markdown || (message.status === 'failed' ? '本次处理未完成，请稍后重试。' : message.status === 'cancelled' ? '本次执行已停止。' : '')" :citations="message.citations" /></template></section>
            <section v-if="activeRun" class="message assistant"><div class="assistant-label"><span class="mini-brand">S</span> SemiBrain</div><div class="run-progress" role="status"><span class="pulse-dot"></span>{{ activeRun.progress || '正在处理' }}</div><RunDetails :run="activeRun" /><button v-if="allowWeb" class="text-button" @click="disableWeb">关闭本次联网</button><button class="text-button" :disabled="cancelling || activeRun.status === 'cancelling'" @click="cancelRun">{{ activeRun.status === 'cancelling' ? '正在停止…' : '停止回答' }}</button><MarkdownAnswer :text="activeRun.body_markdown" :citations="activeRun.citations" streaming /></section>
          </div>
        </div>
        <div class="composer-area"><p v-if="error" class="error" role="alert">{{ error }}</p><div class="composer"><textarea v-model="text" :disabled="running" placeholder="向 SemiBrain 提问…" aria-label="问题输入框" rows="2" @keydown="keydown"></textarea><div class="composer-toolbar"><select v-model="mode" :disabled="running" aria-label="问答模式" @change="allowWeb = false"><option value="quick_qa">快速问答</option><option value="investigation">智能调查</option></select><label class="checkbox web-toggle"><input v-model="allowWeb" type="checkbox" :disabled="running" />联网搜索</label><details ref="sourceMenu" class="source-select"><summary>▤ 资料范围{{ selectedDocuments.length ? ` · ${selectedDocuments.length}` : '' }}</summary><div class="source-menu"><p class="small muted">不勾选时检索全部可访问资料</p><label v-for="doc in knowledge" :key="doc.id" class="checkbox"><input v-model="selectedDocuments" type="checkbox" :value="doc.id" />{{ doc.title }}</label><p class="small muted">将已发布文件作为本轮阅读对象</p><label v-for="doc in knowledge.filter(item => item.asset_id)" :key="doc.asset_id" class="checkbox"><input v-model="attached" type="checkbox" :value="doc.asset_id" />阅读：{{ doc.title }}</label><p v-if="!knowledge.length" class="small muted">暂无已发布资料</p></div></details><button class="send-button" :disabled="!text.trim() || running" @click="send" aria-label="发送问题">↑</button></div></div><p class="composer-note">回答可通过引用核验 · 业务数据为合成演示数据 · Enter 发送，Shift + Enter 换行</p></div>
      </template>
    </main>
    <div v-if="promptPreview" class="modal-backdrop"><section class="small-modal" role="dialog" aria-modal="true" aria-label="脱敏提示词预览"><h2>只读装配预览</h2><p>仅显示本账号且来源仍可访问的已执行模型输入；不包含密钥或隐藏推理。</p><pre class="prompt-preview">{{ promptPreview }}</pre><button class="secondary" @click="promptPreview = ''">关闭</button></section></div>
    <div v-if="settings" class="modal-backdrop"><section class="small-modal" role="dialog" aria-modal="true" aria-label="账号设置"><h2>账号设置</h2><p class="muted">修改密码后需要重新登录。</p><form @submit.prevent="changePassword"><label>当前密码<input v-model="oldPassword" type="password" autocomplete="current-password" required /></label><label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" minlength="8" maxlength="128" required /></label><p v-if="error" class="error">{{ error }}</p><div class="row-actions"><button type="button" class="secondary" @click="settings = false">取消</button><button class="primary">保存密码</button></div></form></section></div>
  </div>
</template>
