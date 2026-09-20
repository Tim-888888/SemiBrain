<script setup lang="ts">
import { onMounted, ref } from 'vue'
defineProps<{ audience: 'user' | 'admin' }>()
const checks = ref<Record<string, string>>({})
onMounted(async () => {
  await Promise.all(['conversation', 'agent', 'business'].map(async (name) => {
    try {
      const response = await fetch(`/foundation-health/${name}`, { signal: AbortSignal.timeout(5000) })
      const data = await response.json()
      checks.value[name] = response.ok && data.status === 'ok' ? '已连接' : '未就绪'
    } catch { checks.value[name] = '暂未连接' }
  }))
})
</script>
<template>
  <div class="shell">
    <aside><div class="brand">SemiBrain<span>半导体知识与调查</span></div><p>{{ audience === 'admin' ? '管理后台' : '用户工作台' }}</p><a :href="audience === 'admin' ? '/' : '/admin/'">{{ audience === 'admin' ? '用户工作台' : '管理后台' }} ↗</a></aside>
    <main><div class="stage">A 阶段 · 基础工程</div><h1>{{ audience === 'admin' ? '管理后台准备中' : '知识与调查，从这里开始' }}</h1><p class="lead">服务连接与构建入口已经建立。登录、知识问答和智能调查将在后续阶段开放。</p><section><h2>服务连接</h2><div class="services"><article v-for="(label, key) in {conversation: '会话服务', agent: 'Agent 服务', business: '业务中台'}" :key="key"><span>{{ label }}</span><strong>{{ checks[key] ?? '检查中' }}</strong></article></div></section><p class="note">当前页面用于基础工程验收，不包含模拟问答或预制调查结果。</p></main>
  </div>
</template>
<style>
:root{font-family:Inter,'Microsoft YaHei',sans-serif;color:#203235;background:#f7f9f8;font-synthesis:none}*{box-sizing:border-box}body{margin:0}.shell{display:flex;min-height:100vh}aside{width:248px;padding:36px 26px;background:white;border-right:1px solid #e3e9e7}.brand{font-size:26px;letter-spacing:-1px;font-weight:700;color:#17644f}.brand span{display:block;font-size:12px;font-weight:400;letter-spacing:1px;color:#7b8a86;margin-top:9px}aside p{margin-top:64px;font-size:14px}a{color:#17644f;font-size:13px}main{max-width:1100px;padding:80px 60px;flex:1}.stage{font-size:12px;letter-spacing:2px;color:#43836e}h1{font-size:36px;font-weight:600;letter-spacing:-1px;margin:20px 0}.lead{color:#778581;line-height:1.9;max-width:680px}section{margin-top:54px}h2{font-size:15px;font-weight:500}.services{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}article{background:white;border:1px solid #e0e8e4;border-radius:12px;padding:25px 22px;display:flex;flex-direction:column;gap:26px}article span{font-size:14px}strong{font-size:13px;color:#35785f}.note{font-size:12px;color:#89938f;margin-top:28px}@media(max-width:780px){aside{width:170px;padding:25px 18px}main{padding:40px 24px}.services{grid-template-columns:1fr}h1{font-size:26px}}
</style>
