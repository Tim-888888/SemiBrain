<script setup lang="ts">
import { rememberFilters } from './page-filters'
import { computed, onMounted, ref } from 'vue'
import { api, type User } from './api'
import ResourceDrawer from './ResourceDrawer.vue'
import { ElMessageBox } from 'element-plus'
import 'element-plus/es/components/message-box/style/css'
const loading=ref(true), busy=ref(false), query=ref(''), role=ref(''), state=ref(''), note=ref('')
const resetOpen=computed({get:()=>!!reset.value,set:(value:boolean)=>{if(!value){reset.value=null;password.value=''}}})
const filtered=computed(()=>users.value.filter(u=>u.username.toLowerCase().includes(query.value.toLowerCase()) && (!role.value || u.role===role.value) && (!state.value || String(u.enabled)===state.value)))
async function toggle(user:User){try{await ElMessageBox.confirm(user.enabled?'停用后此账号将无法继续访问。':'确认恢复此账号的访问权限？',`${user.enabled?'停用':'启用'} ${user.username}`,{confirmButtonText:'确认',cancelButtonText:'取消',type:'warning'});await change(user,{enabled:!user.enabled})}catch{}}

const users = ref<User[]>([]), error = ref(''), reset = ref<User | null>(null), password = ref('')
async function load() { try { users.value = (await api('/admin/v1/users')).items } catch (e) { error.value = (e as Error).message } finally{loading.value=false} }
async function change(user: User, values: object) {
  if(busy.value)return;busy.value=true;error.value = '';note.value=''
  try { await api(`/admin/v1/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ expected_revision: user.revision, ...values }) }); reset.value = null; password.value = ''; note.value='账号已更新。'; await load() }
  catch (e) { error.value = (e as Error).message } finally {busy.value=false}
}
onMounted(load)
rememberFilters('UsersPanel', {query,role,state})
</script>
<template>
  <section class="resource-panel"><div class="page-title"><div><p class="eyebrow">ACCESS MANAGEMENT</p><h1>用户管理</h1><p class="muted">管理账号状态与访问权限。</p></div></div><p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="note" class="notice" role="status">{{note}}</p><p v-if="loading" class="loading-state">正在加载账号…</p><div class="page-toolbar"><input v-model="query" type="search" placeholder="搜索用户名" aria-label="搜索用户"/><select v-model="role" aria-label="账号角色"><option value="">全部角色</option><option value="admin">管理员</option><option value="user">普通用户</option></select><select v-model="state" aria-label="账号状态"><option value="">全部状态</option><option value="true">正常</option><option value="false">已停用</option></select><span class="result-count">{{filtered.length}} 个账号</span></div><p v-if="!loading && !filtered.length && !error" class="empty-card">没有匹配的账号</p><div class="table-card"><table><thead><tr><th>账号</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="user in filtered" :key="user.id"><td><strong>{{ user.username }}</strong><span v-if="user.demo" class="badge">演示</span></td><td>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</td><td>{{ user.enabled ? '正常' : '已停用' }}</td><td><button class="text-button"  :disabled="busy" @click="toggle(user)">{{ user.enabled ? '停用' : '启用' }}</button><button class="text-button" :disabled="busy" @click="reset = user; password = ''">重置密码</button></td></tr></tbody></table></div>
    <ResourceDrawer v-model="resetOpen" :title="'重置 ' + (reset?.username || '') + ' 的密码'" width="480px" :snapshot="password" :busy="busy" :error="error"><p class="muted">重置后，该账号所有旧登录态将失效。</p><form id="user-reset" @submit.prevent="change(reset!, {password})"><label>新密码<input v-model="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password" /></label></form><template #footer><button form="user-reset" class="primary" :disabled="busy">保存新密码</button></template></ResourceDrawer>
  </section>
</template>
