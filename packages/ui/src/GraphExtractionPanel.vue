<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, post } from './api'
import ResourceDrawer from './ResourceDrawer.vue'
const props=defineProps<{open:boolean,documents:any[]}>()
const emit=defineEmits(['update:open','changed'])
const tab=ref('batch'),busy=ref(false),error=ref(''),notice=ref(''),models=ref<string[]>([])
const settings=ref({enabled:false,scope:'wiki',auto_accept:false,model:'deepseek-flash',instructions:''})
const jobs=ref<any[]>([]),pending=ref<any[]>([]),jobCursor=ref<string|null>(null),candidateCursor=ref<string|null>(null)
const selected=ref<string[]>([]),docFilter=ref(''),wikiOnly=ref(true),reviewing=ref<any>(null),correction=ref<any>(null),reason=ref('')
const relations:Record<string,string>={applies_to:'适用于',occurs_in:'发生于',references:'引用',recommended_action:'处理建议',precedes:'先于',cooccurs:'共现／相关',validated_cause:'经验证的因果',uses:'使用',part_of:'组成部分',is_a:'属于类型',produces:'产生',measures:'测量'}
const types:Record<string,string>={product:'产品',process:'工序',equipment:'设备',chamber:'腔体',defect:'缺陷',alarm:'告警',sop:'SOP',action:'维护动作',concept:'概念'}
const labels:Record<string,string>={queued:'排队中',running:'抽取中',succeeded:'处理完成',failed:'处理失败',cancelled:'已取消',superseded:'来源或任务已失效'}
const available=computed(()=>props.documents.filter(d=>(!wikiOnly.value || d.kind==='wiki' || d.path?.startsWith('Wiki/')) && d.title.toLowerCase().includes(docFilter.value.toLowerCase())))
let timer:ReturnType<typeof setInterval>|undefined
async function act(work:()=>Promise<void>){busy.value=true;error.value='';notice.value='';try{await work()}catch(e){error.value=(e as Error).message}finally{busy.value=false}}
async function refresh(append=false){
 const suffix=append && (tab.value==='review'?candidateCursor.value:jobCursor.value)
 const data=await api('/admin/v1/graph/extraction?kind='+ (tab.value==='review'?'candidates&status=pending':'jobs')+(suffix?'&before='+encodeURIComponent(suffix):''))
 if(tab.value==='review'){pending.value=append?[...pending.value,...data.items]:data.items;candidateCursor.value=data.next_cursor}
 else{jobs.value=append?[...jobs.value,...data.items]:data.items;jobCursor.value=data.next_cursor}
}
async function command(path:string,body:object){const current=await api('/admin/v1/graph/extraction');return post('/admin/v1/graph/extraction/'+path,{request_id:crypto.randomUUID(),expected_revision:current.revision,...body})}
async function initialize(){await act(async()=>{const data=await api('/admin/v1/graph/extraction');settings.value=data.policy;jobs.value=data.items;jobCursor.value=data.next_cursor;models.value=(await api('/admin/v1/graph/extraction/models')).models;await refresh()})}
watch(()=>props.open,async value=>{if(timer)clearInterval(timer);if(value){await initialize();timer=setInterval(()=>{if(!busy.value&&!reviewing.value&&['jobs','review'].includes(tab.value))refresh().catch(()=>{})},10000)}})
watch(tab,()=>{if(props.open)act(()=>refresh())})
onUnmounted(()=>{if(timer)clearInterval(timer)})
function pickAll(){selected.value=available.value.slice(0,100).map(d=>d.id)}
async function savePolicy(){await act(async()=>{await command('policy',{policy:settings.value});notice.value='自动构建设置已保存。';emit('changed')})}
async function enqueue(){await act(async()=>{await command('queue',{document_ids:selected.value});selected.value=[];tab.value='jobs';await refresh();notice.value='已加入独立后台队列，资料仍可正常检索。';emit('changed')})}
async function control(job:any,action:string){await act(async()=>{await command('job',{job_id:job.id,action});await refresh();emit('changed')})}
async function openReview(row:any){await act(async()=>{const detail=await api('/admin/v1/graph/extraction/candidates/'+row.id);correction.value=JSON.parse(JSON.stringify(detail.candidate));reason.value='';reviewing.value=detail})}
async function decide(action:string){await act(async()=>{await command('review',{candidate_id:reviewing.value.id,action,reason:reason.value,correction:action==='approve'?correction.value:null});reviewing.value=null;await refresh();notice.value=action==='approve'?'关系已审核入图，后台将更新图谱。':'已拒绝；重新抽取也会保留此决定。';emit('changed')})}
</script>
<template>
 <ResourceDrawer :model-value="open" @update:model-value="emit('update:open',$event)" title="自动构建工程图谱" width="980px">
  <p class="muted">从已发布资料提取实体与关系，保留每条关系的原文依据。人工修正和停用优先于自动结果。</p>
  <div class="tabs" role="tablist" aria-label="图谱构建"><button v-for="(label,key) in {batch:'批量抽取',jobs:'任务进度',review:'待审核',settings:'自动构建设置'}" :key="key" role="tab" :aria-selected="tab===key" @click="tab=key">{{label}}</button></div>
  <p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
  <section v-if="tab==='settings'" class="settings-grid">
   <label class="check"><input type="checkbox" v-model="settings.enabled"/>发布后自动抽取（也会补齐所选范围内的旧资料）</label>
   <label>自动抽取范围<select v-model="settings.scope"><option value="wiki">已发布工程 Wiki</option><option value="all">全部已发布知识资料</option></select></label>
   <label>抽取与核验模型<select v-model="settings.model"><option v-for="model in models" :key="model" :value="model">{{model}}</option></select></label>
   <label class="check"><input type="checkbox" v-model="settings.auto_accept"/>普通关系通过原文核验后自动入图</label>
   <p class="small muted">关闭自动入图时，抽取结果进入待审核；歧义、别名冲突和因果关系始终需要审核。使用独立后台队列，失败最多自动尝试 3 次。</p>
   <label>领域补充说明<textarea v-model="settings.instructions" rows="4" maxlength="2000" placeholder="例如：优先提取测试工序、设备、量测指标及适用关系。"/></label>
   <button class="primary" :disabled="busy" @click="savePolicy">保存自动构建设置</button>
  </section>
  <section v-else-if="tab==='batch'">
   <div class="batch-controls"><input v-model="docFilter" aria-label="搜索抽取资料" placeholder="搜索已发布资料"/><label class="check"><input type="checkbox" v-model="wikiOnly"/>只看工程 Wiki</label><button :disabled="busy" @click="pickAll">选择当前结果（最多100份）</button><button @click="selected=[]">清空选择</button></div>
   <p class="small muted">已选 {{selected.length}} 份。使用当前设置中的模型与入图规则；再次抽取会替代该文档未完成的任务，保留人工维护决定。</p>
   <div class="document-list"><label v-for="doc in available" :key="doc.id" class="check document-choice"><input type="checkbox" :value="doc.id" v-model="selected" :disabled="!selected.includes(doc.id)&&selected.length>=100"/><span>{{doc.title}}</span></label></div>
   <p v-if="!available.length" class="muted">没有匹配的已发布资料。</p>
   <button class="primary" :disabled="busy||!selected.length" @click="enqueue">抽取所选资料</button>
  </section>
  <section v-else-if="tab==='jobs'">
   <button :disabled="busy" @click="act(()=>refresh())">刷新进度</button>
   <p v-if="!jobs.length" class="muted">尚无抽取任务。可以先选择少量 Wiki 验证效果。</p>
   <article v-for="job in jobs" :key="job.id" class="job-card"><strong>{{job.title}}</strong><span class="status">{{labels[job.status] || job.status}}</span><p>{{job.completed}} / {{job.total}} 个片段 · 自动入图 {{job.counts.accepted}} · 待审核 {{job.counts.pending}} · 保留人工决定 {{job.counts.suppressed}}</p><p class="small muted">{{job.config.model}} · 已记录 {{job.total_tokens.toLocaleString()}} Token · {{new Date(job.created_at).toLocaleString()}}</p><p v-if="job.error" class="small">本次处理未完成（{{job.error}}）。可核对来源状态后重试。</p><button v-if="['queued','running'].includes(job.status)" :disabled="busy" @click="control(job,'cancel')">取消任务</button><button v-if="['failed','cancelled'].includes(job.status)" :disabled="busy" @click="control(job,'retry')">重试未完成片段</button></article>
   <button v-if="jobCursor" :disabled="busy" @click="act(()=>refresh(true))">加载更多任务</button>
  </section>
  <section v-else>
   <button :disabled="busy" @click="act(()=>refresh())">刷新待审核</button><p v-if="!pending.length" class="muted">目前没有待审核关系。</p>
   <article v-for="row in pending" :key="row.id" class="job-card"><strong>{{row.candidate.subject.name}} → {{row.candidate.object.name}}</strong><span class="status">{{relations[row.candidate.relation]}}</span><p>{{row.reason}}</p><blockquote>{{row.candidate.quote}}</blockquote><p class="small muted">{{row.title}} · {{row.source_active?'来源有效':'来源已失效，无法批准'}}</p><button :disabled="busy" @click="openReview(row)">查看原文并审核</button></article>
   <button v-if="candidateCursor" :disabled="busy" @click="act(()=>refresh(true))">加载更多待审核关系</button>
  </section>
 </ResourceDrawer>
 <ResourceDrawer :model-value="!!reviewing" @update:model-value="value=>{if(!value)reviewing=null}" title="核对关系与原文" width="850px">
  <template v-if="reviewing&&correction"><p>{{reviewing.title}}</p><pre class="source-text">{{reviewing.source_text || '当前来源已失效，无法继续批准。'}}</pre>
   <div class="entity-edit"><label>起点名称<input v-model="correction.subject.name" maxlength="120"/></label><label>起点类型<select v-model="correction.subject.kind"><option v-for="(label,key) in types" :key="key" :value="key">{{label}}</option></select></label><label>终点名称<input v-model="correction.object.name" maxlength="120"/></label><label>终点类型<select v-model="correction.object.kind"><option v-for="(label,key) in types" :key="key" :value="key">{{label}}</option></select></label></div>
   <p class="small muted">别名：{{correction.subject.aliases.join('、') || '无'}} / {{correction.object.aliases.join('、') || '无'}} <button @click="correction.subject.aliases=[];correction.object.aliases=[]">清除有疑问的别名</button></p>
   <label>关系<select v-model="correction.relation"><option v-for="(label,key) in relations" :key="key" :value="key">{{label}}</option></select></label>
   <label>原文引句<textarea v-model="correction.quote" rows="3"/></label><label>审核说明<textarea v-model="reason" rows="3" :placeholder="correction.relation==='validated_cause'?'至少20字，记录实际工程验证依据':'至少5字，说明批准、修正或拒绝依据'"/></label>
   <p v-if="error" class="error" role="alert">{{error}}</p>
  </template>
  <template #footer><button :disabled="busy||reason.trim().length<5" @click="decide('reject')">拒绝并保留决定</button><button class="primary" :disabled="busy||!reviewing?.source_active||reason.trim().length<(correction?.relation==='validated_cause'?20:5)" @click="decide('approve')">批准关系</button></template>
 </ResourceDrawer>
</template>
<style scoped>
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.tabs button[aria-selected=true]{color:var(--accent);background:var(--nav-selected);font-weight:600}.settings-grid{display:grid;gap:16px}.settings-grid>button{justify-self:start}label{display:block}input:not([type=checkbox]),select,textarea{width:100%;margin-top:6px}.check{display:flex;align-items:center;gap:10px}.check input{width:16px;flex:0 0 16px}.batch-controls{display:flex;align-items:center;flex-wrap:wrap;gap:12px}.batch-controls>input{max-width:300px}.document-list{max-height:440px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin:14px 0}.document-choice{padding:12px 14px;border-bottom:1px solid var(--line)}.job-card{padding:16px;margin:12px 0;border:1px solid var(--line);border-radius:12px;background:#fff}.status{float:right;color:var(--accent)}blockquote{border-left:3px solid var(--line);padding-left:12px;white-space:pre-wrap;max-height:160px;overflow:auto}.source-text{white-space:pre-wrap;max-height:320px;overflow:auto;background:var(--canvas);padding:16px;border-radius:8px;font:inherit}.entity-edit{display:grid;grid-template-columns:2fr 1fr;gap:12px}@media(max-width:600px){.entity-edit{grid-template-columns:1fr}.status{float:none;display:block}}
</style>
