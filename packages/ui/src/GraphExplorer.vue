<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { post } from './api'
import GraphCanvas from './GraphCanvas.vue'
import ResourceDrawer from './ResourceDrawer.vue'
const props=defineProps<{revision:number;refreshKey?:number;types:Record<string,string>;relations:Record<string,string>}>()
const data=ref<any>({entities:[],edges:[],meta:{total:0,returned:0,remaining:0}})
const query=ref(''),kind=ref(''),limit=ref(100),busy=ref(false),error=ref(''),mode=ref('overview'),center=ref(''),anchor=ref(''),resetKey=ref(0)
const detail=ref(''),detailOpen=ref(false),expanded=ref(false),shell=ref<HTMLElement|null>(null),toggle=ref<HTMLButtonElement|null>(null)
const selected=computed(()=>data.value.entities.find((n:any)=>n.id===detail.value))
const evidence=computed(()=>data.value.edges.filter((e:any)=>e.subject_id===detail.value||e.object_id===detail.value))
const names=computed(()=>new Map(data.value.entities.map((n:any)=>[n.id,n.name])))
const visualEdges=computed(()=>[...new Map(data.value.edges.map((e:any)=>[[e.subject_id,e.relation,e.object_id].join('|'),e])).values()] as any[])
let serial=0
async function load(nextMode='overview',id='',append=false){
  const request=++serial;busy.value=true;error.value=''
  try{
    const result=await post('/v1/graph/browse',{mode:nextMode,center:id||null,query:query.value,limit:limit.value,depth:1,kinds:kind.value?[kind.value]:[],visible_ids:append?data.value.entities.map((n:any)=>n.id):[]})
    if(request!==serial)return
    if(!result?.meta||!Array.isArray(result.entities)||!Array.isArray(result.edges))throw new Error('图谱数据暂不可用，请刷新重试。')
    data.value=result;mode.value=nextMode;center.value=nextMode==='ego'?id:'';anchor.value=append?id:''
    if(!append){resetKey.value++;detailOpen.value=false;detail.value=''}
  }catch(e){if(request===serial)error.value=(e as Error).message}
  finally{if(request===serial)busy.value=false}
}
function inspect(id:string){detail.value=id;detailOpen.value=true}
function expand(id:string){if(!busy.value&&!data.value.meta.at_capacity)load('ego',id,true)}
function overview(){query.value='';load()}
async function fullscreen(){expanded.value=!expanded.value;await nextTick();toggle.value?.focus()}
function keys(event:KeyboardEvent){
  if(!expanded.value||detailOpen.value)return
  if(event.key==='Escape'){event.preventDefault();fullscreen()}
  if(event.key==='Tab'){
    const items=Array.from(shell.value?.querySelectorAll<HTMLElement>('button:not(:disabled),input,select,[tabindex="0"]')||[])
    const first=items[0],last=items.at(-1)
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
  }
}
watch(()=>[props.revision,props.refreshKey],()=>load(mode.value,center.value))
onMounted(()=>load())
</script>
<template>
 <div ref="shell" class="graph-explorer" :class="{'graph-expanded':expanded}" :role="expanded?'dialog':undefined" :aria-modal="expanded?'true':undefined" aria-label="工程图谱浏览" @keydown="keys">
  <div class="explorer-toolbar">
   <form class="explorer-search" @submit.prevent="load()">
    <label class="explorer-field">搜索实体<input v-model="query" aria-label="搜索图谱实体" maxlength="200" placeholder="搜索实体，例如 CP、刻蚀、探针台"/></label>
    <button :disabled="busy" class="primary">搜索节点</button>
   </form>
   <div class="explorer-filters">
    <label class="explorer-field">实体类型<select v-model="kind" @change="load()" :disabled="busy"><option value="">全部类型</option><option v-for="(label,value) in types" :value="value" :key="value">{{label}}</option></select></label>
    <label class="explorer-field">每次加载<select v-model.number="limit" @change="load(mode,center)" :disabled="busy"><option :value="50">50 个</option><option :value="100">100 个</option><option :value="200">200 个</option><option :value="500">500 个</option></select></label>
   </div>
   <div class="explorer-actions">
    <button class="secondary" :disabled="busy" @click="overview">返回概览</button>
    <button ref="toggle" class="secondary" @click="fullscreen">{{expanded?'退出全屏':'全屏查看'}}</button>
   </div>
  </div>
  <p v-if="error" class="error" role="alert">{{error}} <button @click="load(mode,center)">重试</button></p>
  <div class="explorer-status" aria-live="polite"><span>{{mode==='ego'?'局部探索':'图谱概览'}} · 已展示 {{data.meta.returned}} / {{data.meta.total}} 个可见实体</span><span v-if="busy">正在加载关系与来源…</span><span v-else-if="data.meta.at_capacity">画布已达 500 个节点，可双击节点切换局部图。</span><span v-else-if="data.meta.truncated">本次范围还有 {{data.meta.remaining}} 个节点未展示</span></div>
  <GraphCanvas v-if="data.entities.length" :nodes="data.entities" :edges="visualEdges" :types="types" :relations="relations" :layout-key="resetKey" :expansion-anchor="anchor" :expand-disabled="busy||data.meta.at_capacity" @select="inspect" @explore="id=>load('ego',id)" @expand="expand"/>
  <p v-else-if="!busy" class="empty">没有匹配的有效实体。可尝试其他名称，或等待资料抽取并审核完成。</p>
  <p class="small muted">单击查看原文 · 双击进入局部图 · Shift＋单击追加邻居 · 虚线圈与 +数字表示尚未加载的邻居</p>
 </div>
 <ResourceDrawer v-model="detailOpen" :title="selected?.name||'实体与来源'" width="620px">
  <template v-if="selected"><p>{{types[selected.kind]}} · {{selected.neighbor_count}} 个可见邻居，{{selected.hidden_count}} 个尚未加载</p><div class="row-actions"><button class="primary" :disabled="busy||!selected.hidden_count||data.meta.at_capacity" @click="expand(detail)">追加邻居（{{selected.hidden_count}}）</button><button :disabled="busy" @click="load('ego',detail)">以此节点为中心</button></div>
   <p class="small muted">以下为当前画布中已加载关系的原文；追加邻居可继续查看其他关系。</p>
   <article v-for="edge in evidence" :key="edge.id" class="panel-card"><h3>{{names.get(edge.subject_id)}} → {{names.get(edge.object_id)}}</h3><span class="status-pill">{{relations[edge.relation]}}</span><blockquote>{{edge.quote}}</blockquote><p>{{edge.source_title}} <span v-if="edge.data_origin==='synthetic'" class="status-pill">合成演示资料</span></p><p v-if="edge.verification_note">{{edge.verification_note}}</p><details><summary>来源定位与版本</summary><p class="small">文档 {{edge.document_id}} · 版本 {{edge.version}} · 片段 {{edge.chunk_id}}</p></details></article>
   <p v-if="!evidence.length">当前尚未加载关联边，可点击“追加邻居”查看。</p>
  </template><p v-else>当前来源或筛选条件已变化，请重新选择节点。</p>
 </ResourceDrawer>
</template>
<style scoped>
.graph-explorer{min-width:0;container:graph-explorer / inline-size}
.explorer-toolbar{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap}
.explorer-search{display:flex;align-items:flex-end;gap:8px;flex:1 1 300px;min-width:0;margin:0}
.explorer-search .explorer-field{flex:1;min-width:0}
.explorer-filters,.explorer-actions{display:flex;align-items:flex-end;gap:12px}
.explorer-actions{margin-left:auto;gap:8px}
/* Override form-label spacing locally so both workspaces share one control baseline. */
.graph-explorer .explorer-toolbar .explorer-field{display:flex;flex-direction:column;gap:6px;margin:0;font-size:12px;line-height:18px;color:var(--muted)}
.graph-explorer .explorer-toolbar input,.graph-explorer .explorer-toolbar select,.graph-explorer .explorer-toolbar button{box-sizing:border-box;height:40px;min-height:40px;margin:0;border-radius:8px;font-size:13px;line-height:20px}
.explorer-toolbar input{width:100%;min-width:0}
.explorer-toolbar select{width:112px}
.explorer-toolbar button{flex-shrink:0;white-space:nowrap;padding:9px 15px}
@container graph-explorer (max-width:860px){.explorer-search{flex-basis:100%}}
@media (max-width:650px) {
 .explorer-toolbar{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
 .explorer-search{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px}
 .explorer-filters,.explorer-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;min-width:0;width:100%;margin:0}
 .explorer-field{min-width:0}
 .graph-explorer .explorer-toolbar input,.graph-explorer .explorer-toolbar select,.graph-explorer .explorer-toolbar button{height:42px;min-height:42px;font-size:13px}
 .explorer-toolbar select,.explorer-actions button{width:100%;min-width:0}
 .explorer-toolbar button{padding:9px 12px}
 .graph-explorer .explorer-status{flex-direction:column;gap:4px;margin-top:14px;line-height:1.6}
}
.explorer-status{display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:16px}.graph-explorer :deep(.interactive-graph){margin:12px 0}.graph-explorer :deep(svg){height:clamp(480px,68vh,1000px)}.graph-expanded{position:fixed;inset:0;z-index:1900;background:var(--canvas,#f5f7fa);padding:18px 24px;display:flex;flex-direction:column;overflow:auto}.graph-expanded :deep(.interactive-graph){flex:1;min-height:300px;display:flex;flex-direction:column}.graph-expanded :deep(svg){height:auto;flex:1;min-height:240px}.empty{padding:80px 20px;text-align:center}blockquote{white-space:pre-wrap;border-left:3px solid var(--border);padding-left:12px;max-height:280px;overflow:auto}.panel-card{margin-top:14px}.panel-card h3{font-size:15px}details{overflow-wrap:anywhere}@media(max-width:700px){.graph-explorer :deep(svg){height:60vh;min-height:360px}.graph-expanded{padding:12px}}
</style>
