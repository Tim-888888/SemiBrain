<script setup lang="ts">
import { ref } from 'vue'
import { api } from './api'
import { mcpError } from './mcp-config'
const props=defineProps<{runId:string}>()
const items=ref<any[]>([]),error=ref(''),busy=ref(false)
async function load(){busy.value=true;error.value='';try{items.value=(await api('/v1/runs/'+props.runId+'/mcp-calls')).items}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
function toggle(event:Event){if(event.target===event.currentTarget && (event.target as HTMLDetailsElement).open)void load()}
const statuses:Record<string,string>={succeeded:'成功',failed:'失败',cancelled:'已停止',running:'调用中',queued:'等待调用',accepted:'已接受'}
</script>
<template><details class="mcp-activity" @toggle="toggle"><summary>MCP 调用记录</summary><button type="button" class="text-button" :disabled="busy" @click="load">{{busy?'加载中…':'刷新'}}</button><p v-if="error" class="error">{{error}}</p><p v-else-if="!busy && !items.length" class="small muted">本轮尚未调用外部 MCP 工具。</p><article v-for="row in items" :key="row.job_id"><strong>{{row.service}} · {{row.tool}}</strong><span> · {{statuses[row.status] || row.status}}</span><p v-if="row.error" class="error">{{mcpError(row.error.code)}}</p><details><summary>参数与结果</summary><pre v-if="row.arguments">{{JSON.stringify(row.arguments,null,2)}}</pre><pre>{{row.content || '等待结果'}}</pre></details></article></details></template>
<style scoped>.mcp-activity{margin:12px 0}.mcp-activity article{border-top:1px solid #e2e8f0;padding:12px 0}.mcp-activity pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;font-size:12px}</style>
