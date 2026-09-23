<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import ComposerAddMenu from './ComposerAddMenu.vue'
import ResizableSidebar from './ResizableSidebar.vue'
import { clipboardImages, imageError, ScrollFollow } from './composer.mjs'
import AuthPanel from './AuthPanel.vue'
import KnowledgePanel from './KnowledgePanel.vue'
import MarkdownAnswer from './MarkdownAnswer.vue'
import RunDetails from './RunDetails.vue'
import RunComparison from './RunComparison.vue'
import UsersPanel from './UsersPanel.vue'
import { api, post, setCsrf, type Message, type Run, type User } from './api'
import './style.css'
const props = defineProps<{ audience: 'admin' | 'user' }>()
const user = ref<User | null>(null), loading = ref(true), view = ref('chat'), error = ref(''), text = ref('')
const conversations = ref<any[]>([]), conversation = ref<any>(null), messages = ref<Message[]>([]), activeRun = ref<Run | null>(null)
const attached = ref<string[]>([])
const mode = ref<'quick_qa' | 'investigation'>('quick_qa'), allowWeb = ref(false)
const multiAvailable = ref(false), multiAgent = ref(false), uploading = ref(false)
const imageUploads = ref<{ asset_id: string; name: string; preview: string }[]>([])
const uploadingPreview = ref(''), imageInput = ref<HTMLInputElement | null>(null)
const imageEnabled = computed(() => mode.value === 'investigation' && multiAgent.value && multiAvailable.value)
const addActions = computed(() => [{ id: 'image', label: '添加图片', disabled: !imageEnabled.value,
  description: imageEnabled.value ? '选择文件，或直接粘贴到输入框' : '请先选择智能调查并开启多 Agent 协作' }])
let uploadController: AbortController | null = null
const submittedStrategy = computed(() => mode.value === 'investigation' ? (multiAgent.value ? 'multi_agent' : 'single_agent') : undefined)
const cancelling = ref(false), promptPreview = ref('')
const knowledge = ref<any[]>([]), selectedDocuments = ref<string[]>([]), sending = ref(false), scrollArea = ref<HTMLElement | null>(null)
const messageColumn = ref<HTMLElement | null>(null), followLatest = ref(true), loadingOlder = ref(false)
const scrollFollow = new ScrollFollow(() => scrollArea.value, value => { followLatest.value = value })
let touchY = 0
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
  const [page, documents, capabilities] = await Promise.all([api('/v1/conversations'), api('/v1/knowledge/documents'), api('/v1/capabilities')])
  if (current !== generation) return
  multiAvailable.value = capabilities.multi_agent === true; conversations.value = page.items; conversationsCursor.value = page.next_cursor
  knowledge.value = documents.items.filter((item: any) => item.active_version)
  const updated = page.items.find((item: any) => item.id === conversation.value?.id)
  if (updated) conversation.value = updated
}
async function signedIn(value: User) { user.value = value; if (adminPage.value) view.value = 'knowledge'; await loadLists() }
function rememberChat(id?: string) {
  if (!user.value) return
  try { const key = `semibrain:${props.audience}:${user.value.id}:conversation`; if (id) sessionStorage.setItem(key, id); else sessionStorage.removeItem(key) } catch { /* Storage-disabled browsers can still use the workspace. */ }
}
async function restoreChat() {
  if (!user.value) return
  try {
    const id = sessionStorage.getItem(`semibrain:${props.audience}:${user.value.id}:conversation`)
    if (id && /^[a-f0-9-]{36}$/i.test(id)) await openChat(conversations.value.find(item => item.id === id) || { id, title: '历史会话', revision: 0 })
  } catch { /* Saved state is a navigation hint; the server authorizes every read. */ }
}
function closeStream() { source?.close(); source = null }
async function bottom(force = false) { const current = generation; if (force) scrollFollow.resume(); await nextTick(); if (current === generation) scrollFollow.sync() }
function scrollKey(event: KeyboardEvent) { if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) scrollFollow.pause() }
function scrollTouch(event: TouchEvent) { const y = event.touches[0]?.clientY ?? touchY; if (y > touchY) scrollFollow.pause(); touchY = y }
watch([scrollArea, messageColumn], ([area, column], _, cleanup) => {
  if (!area || !column) return
  const observer = new ResizeObserver(() => { void bottom() })
  observer.observe(area); observer.observe(column); cleanup(() => observer.disconnect())
}, { flush: 'post' })
function clearImages() {
  uploadController?.abort(); uploadController = null; uploading.value = false
  uploadingPreview.value = ''; imageUploads.value = []
}
function newChat() { rememberChat(); multiAgent.value = false; clearImages(); scrollFollow.reset(); allowWeb.value = false; generation++; closeStream(); conversation.value = null; messages.value = []; activeRun.value = null; error.value = ''; pending = null; selectedDocuments.value = []; attached.value = []; messagesCursor.value = null; view.value = 'chat' }
async function openChat(item: any) {
  const current = ++generation; clearImages(); scrollFollow.reset(); closeStream(); view.value = 'chat'; error.value = ''; pending = null
  try {
    const result = await api(`/v1/conversations/${item.id}/messages`)
    if (generation !== current) return
    multiAgent.value = item.last_investigation_strategy === 'multi_agent'; imageUploads.value = []; conversation.value = item; messages.value = result.items; messagesCursor.value = result.next_cursor; activeRun.value = null; selectedDocuments.value = []; attached.value = []
    const last = messages.value.at(-1)
    conversation.value.revision = Math.max(item.revision || 0, ...messages.value.map(message => message.input_revision))
    rememberChat(item.id)
    if (last?.input_scope) restoreScope(last)
    if (last?.role === 'user') { const state: Run = await api(`/v1/runs/${last.run_id}`); if (generation !== current) return; restoreScope(state); subscribe(last.run_id, current) }
    await bottom()
  } catch (e) { if (generation === current) error.value = (e as Error).message }
}
function restoreScope(state: Partial<Run>) {
  if (!state.input_scope) return
  mode.value = state.input_scope.mode; allowWeb.value = state.input_scope.allow_web && !state.web_disabled
  selectedDocuments.value = state.input_scope.resource_restrictions; attached.value = state.input_scope.attachment_refs
  multiAgent.value = state.input_scope.investigation_strategy === 'multi_agent'
}
function addContent(id: string) { if (id === 'image' && imageEnabled.value) imageInput.value?.click() }
function pickImages(event: Event) {
  const element = event.target as HTMLInputElement, files = Array.from(element.files || [])
  element.value = ''; void uploadImages(files)
}
function pasteImages(event: ClipboardEvent) {
  const files = clipboardImages(event.clipboardData); if (!files.length) return
  // Mixed text/image paste preserves the browser's normal text insertion.
  if (!event.clipboardData?.getData('text/plain')) event.preventDefault()
  void uploadImages(files)
}
async function uploadImages(files: File[]) {
  if (!files.length || running.value) return
  if (!imageEnabled.value) { error.value = '添加图片需要先选择智能调查并开启多 Agent 协作。'; return }
  if (uploading.value) { error.value = '图片正在上传，请完成后再添加。'; return }
  if (attached.value.length + files.length > 10) { error.value = '每轮最多添加 10 个附件（含资料文件），请先移除部分附件。'; return }
  const invalid = files.map(file => [file.name, imageError(file)]).find(([, reason]) => reason)
  if (invalid) { error.value = `${invalid[0]}：${invalid[1]}`; return }
  uploading.value = true; error.value = ''; const current = generation, controller = new AbortController(); uploadController = controller
  try {
    for (const file of files) {
      if (current !== generation || controller.signal.aborted) return
      const preview = await imagePreview(file, controller.signal)
      if (current !== generation || controller.signal.aborted) return
      uploadingPreview.value = preview
      const body = new FormData(); body.append('file', file); body.append('allow_external', 'true')
      const result = await api('/v1/attachments/images', { method: 'POST', body, signal: controller.signal })
      if (current !== generation || controller.signal.aborted) return
      imageUploads.value.push({ asset_id: result.asset_id, name: file.name || result.name, preview: uploadingPreview.value })
      uploadingPreview.value = ''; attached.value.push(result.asset_id)
    }
  } catch (e) { if (current === generation && !controller.signal.aborted) error.value = (e as Error).message }
  finally {
    if (uploadController === controller) {
      uploadingPreview.value = ''; uploading.value = false; uploadController = null
    }
  }
}
function imagePreview(file: File, signal: AbortSignal): Promise<string> {
  // The deployed CSP already permits data images, but deliberately excludes blob URLs.
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    const abort = () => { reader.abort(); reject(new DOMException('Aborted', 'AbortError')) }
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('图片预览读取失败，请重新选择。'))
    reader.onloadend = () => signal.removeEventListener('abort', abort)
    if (signal.aborted) { abort(); return }
    signal.addEventListener('abort', abort, { once: true }); reader.readAsDataURL(file)
  })
}
function removeImage(id: string) {
  imageUploads.value = imageUploads.value.filter(item => item.asset_id !== id)
  attached.value = attached.value.filter(item => item !== id)
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
      activeRun.value = null; restoreScope(state); await loadLists()
    }
  })
  source.addEventListener('access.unavailable', () => {
    if (current !== generation) return
    closeStream(); activeRun.value = null; error.value = '登录、来源或服务状态暂时无法核验。请刷新会话重试。'
  })
}
async function send() {
  if (!text.value.trim() || running.value || uploading.value) return
  if (sourceMenu.value) sourceMenu.value.open = false
  sending.value = true; error.value = ''; const current = generation
  try {
    if (!conversation.value) {
      const requestId = crypto.randomUUID()
      const created = await post('/v1/conversations', { request_id: requestId, title: '新会话' }, requestId)
      if (current !== generation) return
      conversation.value = created
      rememberChat(created.id)
    }
    if (!pending || pending.payload.text !== text.value || pending.payload.mode !== mode.value || pending.payload.investigation_strategy !== submittedStrategy.value || pending.payload.allow_web !== allowWeb.value || JSON.stringify(pending.payload.resource_restrictions) !== JSON.stringify(selectedDocuments.value) || JSON.stringify(pending.payload.attachment_refs) !== JSON.stringify(attached.value) || pending.conversationId !== conversation.value.id) pending = {
      conversationId: conversation.value.id, payload: { request_id: crypto.randomUUID(), expected_revision: conversation.value.revision,
        text: text.value, mode: mode.value, investigation_strategy: submittedStrategy.value, continuation_of: messages.value.at(-1)?.status === 'waiting_input' ? messages.value.at(-1)?.run_id : null, resource_restrictions: [...selectedDocuments.value], attachment_refs: [...attached.value], allow_web: allowWeb.value },
    }
    const submitted = pending
    const accepted = await post(`/v1/conversations/${submitted.conversationId}/messages`, submitted.payload, submitted.payload.request_id)
    if (current !== generation) { await loadLists(); return }
    conversation.value.revision = accepted.input_revision
    messages.value.push({ id: accepted.turn_id, role: 'user', text: submitted.payload.text, run_id: accepted.run_id, input_revision: accepted.input_revision })
    text.value = ''; pending = null; subscribe(accepted.run_id); await bottom(true); await loadLists()
  } catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { sending.value = false }
}
async function olderChats() { try { const page = await api('/v1/conversations?after=' + conversationsCursor.value); conversations.value.push(...page.items); conversationsCursor.value = page.next_cursor } catch (e) { error.value = (e as Error).message } }
async function olderMessages() {
  if (loadingOlder.value) return
  const current = generation; loadingOlder.value = true; scrollFollow.pause()
  try {
    const page = await api(`/v1/conversations/${conversation.value.id}/messages?before=${messagesCursor.value}`)
    if (current !== generation) return
    const area = scrollArea.value, edge = area?.getBoundingClientRect().top || 0
    const anchor = Array.from(area?.querySelectorAll<HTMLElement>('[data-message-id]') || []).find(node => node.getBoundingClientRect().bottom > edge)
    const offset = anchor?.getBoundingClientRect().top || 0
    messages.value.unshift(...page.items); messagesCursor.value = page.next_cursor
    await nextTick()
    if (current === generation && area && anchor) { area.scrollTop += anchor.getBoundingClientRect().top - offset; scrollFollow.lastTop = area.scrollTop }
  } catch (e) { if (current === generation) error.value = (e as Error).message }
  finally { loadingOlder.value = false }
}
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
onMounted(async () => { try { const result = await api('/v1/auth/me'); setCsrf(result.csrf); await signedIn(result.user); await restoreChat() } catch { user.value = null } finally { loading.value = false } })
onUnmounted(() => { closeStream(); clearImages() })
</script>
<template>
  <div v-if="loading" class="loading-page">正在打开 SemiBrain…</div>
  <AuthPanel v-else-if="!user" @signed-in="signedIn" />
  <div v-else-if="denied" class="loading-page"><h1>此账号没有管理权限</h1><p><a href="/">返回工作台</a></p></div>
  <div v-else class="workspace">
    <ResizableSidebar><a class="wordmark" href="/"><span class="brand-icon">S</span> SemiBrain</a><span v-if="adminPage" class="admin-caption">管理控制台</span>
      <button v-if="!adminPage" class="new-chat" @click="newChat">＋ 新会话</button>
      <nav><button v-if="!adminPage" :class="{ active: view === 'chat' }" @click="view = 'chat'">◈ 对话工作台</button><button :class="{ active: view === 'knowledge' }" @click="view = 'knowledge'">▤ 知识库</button><button v-if="adminPage" :class="{ active: view === 'users' }" @click="view = 'users'">♙ 用户管理</button><button v-if="adminPage" :class="{ active: view === 'comparison' }" @click="view = 'comparison'">运行对照</button></nav>
      <div v-if="!adminPage" class="history"><p class="eyebrow">最近会话</p><button v-for="item in conversations" :key="item.id" :title="item.title" :class="{ selected: conversation?.id === item.id }" @click="openChat(item)">{{ item.title }}</button><button v-if="conversationsCursor" @click="olderChats">加载更早会话</button><p v-if="!conversations.length" class="small muted">你的会话会保存在这里</p></div>
      <div class="sidebar-bottom"><a v-if="user.role === 'admin' && !adminPage" class="admin-link" href="/admin/">管理控制台 ↗</a><a v-if="adminPage" class="admin-link" href="/">返回工作台 ↗</a><div class="profile"><span class="avatar">{{ user.username.slice(0, 1).toUpperCase() }}</span><div><strong>{{ user.username }}</strong><small>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</small></div><button class="text-button" @click="settings = true" aria-label="账号设置">⚙</button></div><button class="text-button logout" @click="logout">退出登录</button></div>
    </ResizableSidebar>
    <main class="main-panel"><header class="topbar"><span>{{ view === 'chat' ? conversation?.title || '对话工作台' : view === 'knowledge' ? '知识库' : view === 'comparison' ? '运行对照' : '用户管理' }}</span><span class="workspace-tag">半导体知识空间</span></header>
      <KnowledgePanel v-if="view === 'knowledge'" :manage="adminPage && user.role === 'admin'" />
      <UsersPanel v-else-if="view === 'users'" /><RunComparison v-else-if="view === 'comparison'" />
      <template v-else>
        <div ref="scrollArea" class="conversation-scroll" tabindex="0" aria-label="对话记录" @scroll.passive="scrollFollow.scrolled()" @wheel.passive="$event.deltaY < 0 && scrollFollow.pause()" @keydown="scrollKey" @touchstart.passive="touchY = $event.touches[0]?.clientY || 0" @touchmove.passive="scrollTouch">
          <div v-if="!messages.length && !activeRun" class="welcome"><span class="welcome-symbol">✳</span><p class="eyebrow">SEMI BRAIN / KNOWLEDGE ASSISTANT</p><h1>从一个问题开始</h1><p>查阅资料，理解工艺，核验每一条来源。</p><div class="starter-grid"><button @click="text = '知识库中有哪些关于测试良率的资料？'">▤ 查找专业资料<span>从已发布文档中寻找证据 ↗</span></button><button @click="text = '请列出可查询的合成演示批次。'">▦ 查询演示数据<span>了解批次与测试上下文 ↗</span></button></div></div>
          <div ref="messageColumn" class="message-column"><button v-if="messagesCursor" class="text-button" :disabled="loadingOlder" @click="olderMessages">{{ loadingOlder ? '正在加载…' : '加载更早消息' }}</button><section v-for="message in messages" :key="message.id" :data-message-id="message.id" :class="['message', message.role]"><div v-if="message.role === 'user'" class="question-bubble">{{ message.text }}</div><template v-else><div class="assistant-label"><span class="mini-brand">S</span> SemiBrain<span v-if="message.status === 'failed'" class="muted">处理未完成</span></div><RunDetails :run="message" /><button v-if="user.role === 'admin' && ['single_agent', 'multi_agent'].includes(message.strategy || '')" class="text-button" @click="showPrompt(message.run_id)">查看脱敏提示词</button><MarkdownAnswer :text="message.body_markdown || (message.status === 'failed' ? '本次处理未完成，请稍后重试。' : message.status === 'cancelled' ? '本次执行已停止。' : '')" :citations="message.citations" :artifacts="message.artifacts" /></template></section>
            <section v-if="activeRun" class="message assistant"><div class="assistant-label"><span class="mini-brand">S</span> SemiBrain</div><div class="run-progress" role="status"><span class="pulse-dot"></span>{{ activeRun.progress || '正在处理' }}</div><RunDetails :run="activeRun" /><button v-if="allowWeb" class="text-button" @click="disableWeb">关闭本次联网</button><button class="text-button" :disabled="cancelling || activeRun.status === 'cancelling'" @click="cancelRun">{{ activeRun.status === 'cancelling' ? '正在停止…' : '停止回答' }}</button><MarkdownAnswer :text="activeRun.body_markdown" :citations="activeRun.citations" :artifacts="activeRun.artifacts" streaming /></section>
          </div>
        </div>
        <div class="composer-area">
          <button v-if="!followLatest && (messages.length || activeRun)" type="button" class="latest-button" @click="bottom(true)">↓ 回到最新</button>
          <p v-if="error" class="error" role="alert">{{ error }}</p>
          <div class="composer">
            <div v-if="imageUploads.some(image => attached.includes(image.asset_id)) || uploading" class="composer-images" aria-label="本轮图片附件">
              <div v-for="image in imageUploads.filter(image => attached.includes(image.asset_id))" :key="image.asset_id" class="composer-image">
                <img :src="image.preview" :alt="image.name" /><span :title="image.name">{{ image.name }}</span>
                <button type="button" :disabled="running || uploading" :aria-label="`移除图片 ${image.name}`" @click="removeImage(image.asset_id)">×</button>
              </div>
              <div v-if="uploading" class="composer-image is-uploading" role="status"><img v-if="uploadingPreview" :src="uploadingPreview" alt="正在上传的图片" /><span>正在上传…</span></div>
            </div>
            <textarea v-model="text" :disabled="running" placeholder="向 SemiBrain 提问…" aria-label="问题输入框" rows="2" @keydown="keydown" @paste="pasteImages"></textarea>
            <input ref="imageInput" class="image-file-input" type="file" accept="image/png,image/jpeg,image/webp" multiple :disabled="running || uploading || !imageEnabled" tabindex="-1" aria-label="选择图片文件" @change="pickImages" />
            <div class="composer-toolbar">
              <ComposerAddMenu :disabled="running || uploading" :actions="addActions" @select="addContent" />
              <select v-model="mode" :disabled="running || uploading" aria-label="问答模式" @change="allowWeb = false"><option value="quick_qa">快速问答</option><option value="investigation">智能调查</option></select>
              <label v-if="mode === 'investigation'" class="checkbox web-toggle"><input v-model="multiAgent" type="checkbox" :disabled="running || uploading || (!multiAvailable && !multiAgent)" />多 Agent 协作</label>
              <label class="checkbox web-toggle"><input v-model="allowWeb" type="checkbox" :disabled="running" />联网搜索</label>
              <details ref="sourceMenu" class="source-select"><summary>▤ 资料范围{{ selectedDocuments.length ? ` · ${selectedDocuments.length}` : '' }}</summary><div class="source-menu"><p class="small muted">不勾选时检索全部可访问资料</p><label v-for="doc in knowledge" :key="doc.id" class="checkbox"><input v-model="selectedDocuments" :disabled="running || uploading" type="checkbox" :value="doc.id" />{{ doc.title }}</label><p class="small muted">将已发布文件作为本轮阅读对象</p><label v-for="doc in knowledge.filter(item => item.asset_id)" :key="doc.asset_id" class="checkbox"><input v-model="attached" :disabled="running || uploading" type="checkbox" :value="doc.asset_id" />阅读：{{ doc.title }}</label><label v-for="image in imageUploads" :key="image.asset_id" class="checkbox"><input v-model="attached" :disabled="running || uploading" type="checkbox" :value="image.asset_id" />图片：{{ image.name }}</label><p v-if="attached.some(id => !knowledge.some(doc => doc.asset_id === id) && !imageUploads.some(image => image.asset_id === id))" class="small muted">已继承上一轮图片附件 <button type="button" class="text-button" @click="attached = attached.filter(id => knowledge.some(doc => doc.asset_id === id))">移除</button></p><p v-if="!knowledge.length" class="small muted">暂无已发布资料</p></div></details>
              <button class="send-button" :disabled="!text.trim() || running || uploading" @click="send" aria-label="发送问题">↑</button>
            </div>
          </div>
          <p class="composer-note">回答可通过引用核验 · 业务数据为合成演示数据 · Enter 发送，Shift + Enter 换行</p>
        </div>
      </template>
    </main>
    <div v-if="promptPreview" class="modal-backdrop"><section class="small-modal" role="dialog" aria-modal="true" aria-label="脱敏提示词预览"><h2>只读装配预览</h2><p>仅显示本账号且来源仍可访问的已执行模型输入；不包含密钥或隐藏推理。</p><pre class="prompt-preview">{{ promptPreview }}</pre><button class="secondary" @click="promptPreview = ''">关闭</button></section></div>
    <div v-if="settings" class="modal-backdrop"><section class="small-modal" role="dialog" aria-modal="true" aria-label="账号设置"><h2>账号设置</h2><p class="muted">修改密码后需要重新登录。</p><form @submit.prevent="changePassword"><label>当前密码<input v-model="oldPassword" type="password" autocomplete="current-password" required /></label><label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" minlength="8" maxlength="128" required /></label><p v-if="error" class="error">{{ error }}</p><div class="row-actions"><button type="button" class="secondary" @click="settings = false">取消</button><button class="primary">保存密码</button></div></form></section></div>
  </div>
</template>
