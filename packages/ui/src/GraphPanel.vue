<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, post } from './api'
import MarkdownAnswer from './MarkdownAnswer.vue'
const props = defineProps<{ manage:boolean }>()
const catalog = ref<any>({entities:[],edges:[],revision:0}), docs = ref<any[]>([]), chunks = ref<any[]>([])
const busy = ref(false), error = ref(''), notice = ref(''), query = ref(''), result = ref<any>(null)
const edit = ref(false), form = ref<any>({}), from = ref(''), to = ref(''), reason = ref('')
const types:Record<string,string> = {product:'产品',process:'工序',equipment:'设备',chamber:'腔体',defect:'缺陷',alarm:'告警',sop:'SOP',action:'维护动作',concept:'概念'}
const relations:Record<string,string> = {applies_to:'适用于',occurs_in:'发生于',references:'引用',recommended_action:'处理建议',precedes:'先于',cooccurs:'共现／相关',validated_cause:'经验证的因果'}
const nodes = computed(() => catalog.value.entities.filter((n:any) => n.id === n.canonical_id).slice(0,30))
const links = computed(() => catalog.value.edges.filter((e:any) => nodes.value.some((n:any) => n.id === canonical(e.subject_id)) && nodes.value.some((n:any) => n.id === canonical(e.object_id))))
function entity(id:string) { return catalog.value.entities.find((n:any) => n.id === id) }
function canonical(id:string) { return entity(id)?.canonical_id || id }
function point(id:string) { const index = nodes.value.findIndex((n:any) => n.id === canonical(id)); const angle = index * Math.PI * 2 / Math.max(1,nodes.value.length); return {x:420+Math.cos(angle)*310,y:220+Math.sin(angle)*170} }
async function load() {
  catalog.value = await api('/v1/graph/catalog')
  if (props.manage) docs.value = (await api('/v1/knowledge/documents')).items.filter((d:any) => d.active_version)
}
async function act(work:() => Promise<void>) { busy.value = true; error.value = ''; notice.value = ''; try { await work() } catch(e) { error.value=(e as Error).message } finally { busy.value=false } }
function create() { form.value={subject:{name:'',kind:'concept'},object:{name:'',kind:'concept'},relation:'cooccurs',document_id:'',version:'',chunk_id:'',quote:'',verification_note:'',expires:'',enabled:true,request_id:crypto.randomUUID()};chunks.value=[];edit.value=true }
async function selectSource() { await act(async () => {
  form.value.version=docs.value.find(d => d.id === form.value.document_id)?.active_version
  chunks.value=(await api(`/admin/v1/knowledge/documents/${form.value.document_id}/versions/${form.value.version}/chunks`)).items
  form.value.chunk_id='';form.value.quote=''
}) }
function selectChunk() { form.value.quote=chunks.value.find(c => c.id === form.value.chunk_id)?.text.slice(0,2000) || '' }
async function save() { await act(async () => {
  const {expires,...value}=form.value
  await post('/admin/v1/graph/edges',{...value,expected_revision:catalog.value.revision,valid_until:expires?new Date(expires+'T23:59:59+08:00').toISOString():null})
  edit.value=false;await load();notice.value='关系已保存。请重建图谱投影后用于关联检索。'
}) }
async function disable(edge:any) { await act(async () => {
  await post('/admin/v1/graph/edges',{request_id:crypto.randomUUID(),expected_revision:catalog.value.revision,edge_id:edge.id,
    subject:{name:entity(edge.subject_id).name,kind:entity(edge.subject_id).kind},object:{name:entity(edge.object_id).name,kind:entity(edge.object_id).kind},
    relation:edge.relation,document_id:edge.document_id,version:edge.version,chunk_id:edge.chunk_id,quote:edge.quote,
    verification_note:edge.verification_note,valid_until:edge.valid_until,enabled:false})
  await load();result.value=null;notice.value='关系已停用，当前检索立即生效。'
}) }
async function rebuild() { await act(async () => { await post('/admin/v1/graph/rebuild',{request_id:crypto.randomUUID(),expected_revision:catalog.value.revision});await load();notice.value='图谱投影已重建。' }) }
async function merge() { await act(async () => { await post('/admin/v1/graph/merge',{request_id:crypto.randomUUID(),expected_revision:catalog.value.revision,source_id:from.value,target_id:to.value,reason:reason.value});await load();notice.value='实体已合并，原始名称和映射仍保留。请重建图谱投影。' }) }
async function search() { await act(async () => { result.value=await post('/v1/graph/search',{query:query.value,depth:2,top_k:5}) }) }
onMounted(() => act(load))
</script>
<template>
  <section class="resource-panel">
    <div class="page-title"><div><h1>工程知识图谱</h1><p class="muted">沿实体关系查找原文。共现、相关和处理建议不等同于已证明的因果。</p></div><button v-if="manage" class="primary" :disabled="busy" @click="create">登记关系</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <p class="small muted">{{ catalog.configured ? '图谱已配置' : '图谱未配置，查询使用文本检索' }} · 关系版本 {{ catalog.revision }} · 投影版本 {{ catalog.projected_revision }} <button v-if="manage" class="text-button" :disabled="busy || !catalog.configured" @click="rebuild">重建投影</button></p>
    <form class="form-row" @submit.prevent="search"><input aria-label="图谱查询" v-model="query" maxlength="200" placeholder="输入工序、设备或缺陷名称" required/><button class="primary" :disabled="busy">查找关联原文</button></form>
    <div v-if="nodes.length" class="graph-canvas"><svg viewBox="0 0 840 440" role="img" aria-label="已授权实体关系图"><defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="24" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#789385"/></marker></defs><g v-for="edge in links" :key="edge.id"><line :x1="point(edge.subject_id).x" :y1="point(edge.subject_id).y" :x2="point(edge.object_id).x" :y2="point(edge.object_id).y" stroke="#abc4b7" marker-end="url(#graph-arrow)"/><title>{{ relations[edge.relation] }}</title></g><g v-for="node in nodes" :key="node.id"><circle :cx="point(node.id).x" :cy="point(node.id).y" r="13" fill="#1a7866"/><text :x="point(node.id).x" :y="point(node.id).y+30" text-anchor="middle" font-size="12">{{ node.name.slice(0,16) }}</text><title>{{ node.name }} · {{ types[node.kind] }}</title></g></svg><p class="small muted">展示最多 30 个授权实体；关系类型与出处见下表。</p></div>
    <p v-else class="muted">尚无可见的有效关系，可继续使用文本检索。</p>
    <form v-if="manage && edit" class="upload-card" @submit.prevent="save"><h2>登记有来源的关系</h2><fieldset :disabled="busy"><div class="form-row"><label>起点名称<input v-model="form.subject.name" maxlength="120" required/></label><label>起点类型<select v-model="form.subject.kind"><option v-for="(label,key) in types" :value="key">{{ label }}</option></select></label><label>终点名称<input v-model="form.object.name" maxlength="120" required/></label><label>终点类型<select v-model="form.object.kind"><option v-for="(label,key) in types" :value="key">{{ label }}</option></select></label></div>
      <label>关系类型<select v-model="form.relation"><option v-for="(label,key) in relations" :value="key">{{ label }}</option></select></label>
      <label>来源文档<select v-model="form.document_id" @change="selectSource" required><option value="" disabled>请选择已发布来源</option><option v-for="doc in docs" :value="doc.id">{{ doc.title }}</option></select></label>
      <label>来源片段<select v-model="form.chunk_id" @change="selectChunk" required><option value="" disabled>请选择原文片段</option><option v-for="chunk in chunks" :value="chunk.id">{{ chunk.context_header || chunk.text.slice(0,90) }}</option></select></label>
      <label>支持关系的原文引句<textarea v-model="form.quote" rows="5" minlength="6" maxlength="2000" required/></label><p class="small muted">保留原文中的连续引句，不能改写成原文没有的结论。</p>
      <label>工程验证说明<textarea v-model="form.verification_note" maxlength="1000" :minlength="form.relation==='validated_cause'?20:0" :required="form.relation==='validated_cause'" placeholder="因果关系必须记录验证依据；其他关系可补充适用边界"/></label><label>有效期至（可留空）<input type="date" v-model="form.expires"/></label>
      <button class="primary">保存关系</button><button class="secondary" type="button" @click="edit=false">取消</button></fieldset></form>
    <details v-if="manage && nodes.length>1" class="upload-card"><summary>合并同名实体</summary><form @submit.prevent="merge"><div class="form-row"><label>原实体<select v-model="from" required><option value="" disabled>请选择</option><option v-for="n in nodes" :value="n.id">{{ n.name }} · {{ types[n.kind] }}</option></select></label><label>合并到<select v-model="to" required><option value="" disabled>请选择</option><option v-for="n in nodes.filter((v:any)=>v.id!==from)" :value="n.id">{{ n.name }} · {{ types[n.kind] }}</option></select></label></div><label>合并依据<input v-model="reason" minlength="5" maxlength="500" required/></label><button class="primary" :disabled="busy">合并并保留映射</button></form></details>
    <div v-if="catalog.edges.length" class="table-scroll"><table><thead><tr><th>起点 → 终点</th><th>关系</th><th>出处与有效期</th><th v-if="manage">操作</th></tr></thead><tbody><tr v-for="edge in catalog.edges" :key="edge.id"><td>{{ entity(edge.subject_id)?.name }} → {{ entity(edge.object_id)?.name }}</td><td>{{ relations[edge.relation] }}</td><td><details><summary>查看原文与版本</summary><blockquote>{{ edge.quote }}</blockquote><p class="small">文档 {{ edge.document_id }} · 版本 {{ edge.version }} · {{ edge.valid_until || '未设到期日' }}</p><p v-if="edge.verification_note">{{ edge.verification_note }}</p></details></td><td v-if="manage"><button class="secondary" :disabled="busy" @click="disable(edge)">停用</button></td></tr></tbody></table></div>
    <section v-if="result" class="upload-card"><h2>关联原文</h2><p class="muted">{{ result.retrieval.graph_fallback ? '本次使用文本检索补充' : '从授权图谱找到来源，已核验发布状态' }}</p><article v-for="item in result.evidence" :key="item.chunk_id || item._id"><h3>{{ item.title }}</h3><MarkdownAnswer :text="item.text" :image-refs="item.image_refs"/></article><p v-if="!result.evidence.length">未找到当前可读的匹配资料。</p></section>
  </section>
</template>
<style scoped>
label { display:block; margin:12px 0; } input,select,textarea { width:100%; } .form-row { display:flex; gap:12px; flex-wrap:wrap; } .form-row>* { flex:1;min-width:140px; } .graph-canvas { margin:18px 0; border:1px solid #dce6df; border-radius:12px; } svg { width:100%;max-height:440px; } .table-scroll{overflow:auto} table{width:100%;border-collapse:collapse} td,th{padding:12px;border-bottom:1px solid #dce6df;text-align:left} blockquote{white-space:pre-wrap;max-height:240px;overflow:auto}
</style>
