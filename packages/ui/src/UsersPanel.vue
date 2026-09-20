<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type User } from './api'
const users = ref<User[]>([]), error = ref(''), reset = ref<User | null>(null), password = ref('')
async function load() { try { users.value = (await api('/admin/v1/users')).items } catch (e) { error.value = (e as Error).message } }
async function change(user: User, values: object) {
  error.value = ''
  try { await api(`/admin/v1/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ expected_revision: user.revision, ...values }) }); reset.value = null; password.value = ''; await load() }
  catch (e) { error.value = (e as Error).message }
}
onMounted(load)
</script>
<template>
  <section class="resource-panel"><div class="page-title"><div><p class="eyebrow">ACCESS MANAGEMENT</p><h1>用户管理</h1><p class="muted">管理账号状态与访问权限。</p></div></div><p v-if="error" class="error" role="alert">{{ error }}</p><div class="table-card"><table><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="user in users" :key="user.id"><td><strong>{{ user.username }}</strong><span v-if="user.demo" class="badge">演示</span></td><td>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</td><td>{{ user.enabled ? '正常' : '已停用' }}</td><td><button class="text-button" @click="change(user, { enabled: !user.enabled })">{{ user.enabled ? '停用' : '启用' }}</button><button class="text-button" @click="reset = user">重置密码</button></td></tr></tbody></table></div>
    <div v-if="reset" class="modal-backdrop"><section class="small-modal" role="dialog" aria-label="重置密码" aria-modal="true"><h2>重置 {{ reset.username }} 的密码</h2><p class="muted">重置后，该账号所有旧登录态将失效。</p><form @submit.prevent="change(reset!, { password })"><label>新密码<input v-model="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password" /></label><div class="row-actions"><button type="button" class="secondary" @click="reset = null">取消</button><button class="primary">保存新密码</button></div></form></section></div>
  </section>
</template>
