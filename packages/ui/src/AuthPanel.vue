<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, post, setCsrf, type User } from './api'
const emit = defineEmits<{ signedIn: [user: User] }>()
const mode = ref<'login' | 'register'>('login')
const username = ref(''), password = ref(''), answer = ref(''), image = ref(''), challenge = ref('')
const error = ref(''), busy = ref(false), refreshing = ref(false)
async function refresh() {
  if (refreshing.value) return
  refreshing.value = true; challenge.value = ''
  answer.value = ''
  try {
    const data = await api(`/v1/auth/captcha?purpose=${mode.value}`)
    image.value = data.image; challenge.value = data.challenge_id
  } catch (e) { error.value = (e as Error).message }
  finally { refreshing.value = false }
}
async function switchMode(next: 'login' | 'register') { if (refreshing.value || busy.value) return; mode.value = next; error.value = ''; await refresh() }
async function submit() {
  if (busy.value || refreshing.value || !challenge.value) return
  busy.value = true; error.value = ''
  try {
    const result = await post(`/v1/auth/${mode.value}`, { username: username.value, password: password.value, challenge_id: challenge.value, answer: answer.value })
    setCsrf(result.csrf); emit('signedIn', result.user)
  } catch (e) { error.value = (e as Error).message; await refresh() }
  finally { busy.value = false }
}
onMounted(refresh)
</script>
<template>
  <main class="auth-page">
    <section class="auth-story">
      <a class="wordmark" href="/"><span class="brand-icon">S</span> SemiBrain</a>
      <div><p class="eyebrow">SEMICONDUCTOR KNOWLEDGE WORKSPACE</p><h1>让知识有出处，<br>让分析有依据。</h1><p class="story-copy">连接半导体文档与业务数据，<br>把问题变成可追溯的回答。</p></div>
      <p class="muted small">半导体知识 · 数据查询 · 来源追溯</p>
    </section>
    <section class="auth-card">
      <div class="auth-tabs"><button :class="{ selected: mode === 'login' }" @click="switchMode('login')">登录</button><button :class="{ selected: mode === 'register' }" @click="switchMode('register')">注册账号</button></div>
      <h2>{{ mode === 'login' ? '欢迎回来' : '创建你的工作空间' }}</h2>
      <p class="muted">{{ mode === 'login' ? '登录后继续你的知识探索。' : '设置账号密码即可使用，无需邮箱认证。' }}</p>
      <form @submit.prevent="submit">
        <label>用户名<input v-model.trim="username" autocomplete="username" required minlength="4" maxlength="64" pattern="[A-Za-z0-9_.@+\x2d]{4,64}" title="4–64 位，可用英文字母、数字、下划线、点、@、加号或短横线" placeholder="4–64 位，支持邮箱形式的用户名" /></label>
        <p class="small muted">邮箱仅作为用户名，无需验证，也不会发送邮件。</p>
        <label>密码<input v-model="password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" required :minlength="mode === 'register' ? 8 : 1" maxlength="128" :placeholder="mode === 'register' ? '设置 8–128 位密码' : '输入密码'" /></label>
        <label>图形验证码<div class="captcha-row"><input v-model="answer" autocomplete="off" required maxlength="5" placeholder="输入图中字符" /><button class="captcha-image" :disabled="refreshing || busy" type="button" @click="refresh" aria-label="刷新验证码"><img v-if="image" :src="image" alt="图形验证码，点击可刷新" /></button></div></label>
        <p class="small muted">验证码 3 分钟内有效；点击图片可换一张。</p>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
        <button class="primary wide" :disabled="busy || refreshing || !challenge">{{ busy ? '正在处理…' : mode === 'login' ? '登录 SemiBrain' : '创建账号并登录' }}</button>
      </form>
    </section>
  </main>
</template>
