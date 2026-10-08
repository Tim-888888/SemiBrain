<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from './api'
import UiIcon from './UiIcon.vue'
import { useDetailsDismiss } from './use-details-dismiss'
const props=defineProps<{modelValue:string[]|null;multiAgent:boolean;disabled?:boolean;documentOnly?:boolean}>()
const emit=defineEmits<{ 'update:modelValue':[value:string[]|null] }>()
const services=ref<any[]>([]), loading=ref(false), error=ref(''), expanded=ref(false)
const menu = ref<HTMLDetailsElement | null>(null)
useDetailsDismiss(menu)
let generation=0
async function load(){const current=++generation;loading.value=true;error.value='';try{const result=await api('/v1/mcp/services?multi_agent='+props.multiAgent);if(current===generation)services.value=result.services}catch(e){if(current===generation)error.value=(e as Error).message}finally{if(current===generation)loading.value=false}}
function toggle(event:Event){expanded.value=(event.target as HTMLDetailsElement).open;if(expanded.value)void load()}
function select(id:string,checked:boolean){const selected=new Set(props.modelValue??services.value.map(s=>s.id));checked?selected.add(id):selected.delete(id);emit('update:modelValue',[...selected])}
watch(()=>props.multiAgent,()=>{if(expanded.value)void load()})
</script>
<template>
<details ref="menu" class="source-select mcp-select" @toggle="toggle"><summary><UiIcon name="mcp" :size="16"/> MCP 工具{{modelValue===null?'':modelValue.length?' · '+modelValue.length:' · 关闭'}}</summary>
  <div class="source-menu"><strong>本次调查可用的 MCP</strong><p v-if="documentOnly" class="notice">当前限定了知识库文档，本轮不使用外部 MCP；清除文档范围后恢复。</p><p class="small muted">选择服务后，Agent 根据问题按需调用。</p>
    <label class="checkbox"><input type="checkbox" :checked="modelValue===null" :disabled="disabled || documentOnly" @change="emit('update:modelValue',($event.target as HTMLInputElement).checked?null:[])"/>使用管理员授权的服务</label>
    <p v-if="loading" class="small muted">正在加载…</p><p v-if="error" class="error">{{error}} <button type="button" @click="load">重试</button></p>
    <label v-for="service in services" :key="service.id" class="mcp-choice"><input type="checkbox" :checked="modelValue===null || modelValue.includes(service.id)" :disabled="disabled || documentOnly || loading" @change="select(service.id,($event.target as HTMLInputElement).checked)"/><span>{{service.label}}<small>{{service.usage_instructions || service.tool_count+' 个授权工具'}}</small></span></label>
    <p v-if="!loading && !error && !services.length" class="small muted">暂无可用服务，请联系管理员接入并授权。</p><p v-if="modelValue?.some(id=>!services.some(s=>s.id===id))" class="small muted">部分已选服务当前不可用，运行时仍以有效授权为准。</p>
  </div>
</details>
</template>
<style scoped>.mcp-select summary{display:flex;align-items:center;gap:5px}.mcp-choice{display:flex;gap:8px;align-items:flex-start!important;padding:9px 0}.mcp-choice input{margin-top:5px}.mcp-choice small{display:block;color:var(--text-muted,#718096);font-size:12px;white-space:normal}.source-menu{min-width:300px;max-width:min(380px,85vw);text-align:left}</style>
