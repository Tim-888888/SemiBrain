import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import Skills from '../src/SkillsPanel.vue'
import MCP from '../src/MCPPanel.vue'
import Config from '../src/AgentConfiguration.vue'
import Knowledge from '../src/KnowledgePanel.vue'
import Wiki from '../src/WikiPanel.vue'
import Memory from '../src/MemoryPanel.vue'
import Users from '../src/UsersPanel.vue'
import Evaluation from '../src/EvaluationPanel.vue'
import Comparison from '../src/RunComparison.vue'
import Operations from '../src/OperationsPanel.vue'
import Graph from '../src/GraphPanel.vue'
import GraphCanvas from '../src/GraphCanvas.vue'
import Drawer from '../src/ResourceDrawer.vue'
import { confirmNavigation } from '../src/editor-state'
import { api, post } from '../src/api'
import { documentCatalog } from '../src/document-catalog'
import { ElMessageBox } from 'element-plus'
vi.mock('../src/api',()=>({api:vi.fn(),post:vi.fn()}))
vi.mock('../src/document-catalog',()=>({documentCatalog:vi.fn()}))
vi.mock('element-plus/es/components/drawer/style/css',()=>({}))
vi.mock('element-plus/es/components/message-box/style/css',()=>({}))
vi.mock('element-plus',()=>({ElMessageBox:{confirm:vi.fn()},ElConfigProvider:defineComponent({props:['locale'],template:'<slot/>'}),ElDrawer:defineComponent({props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'})}))
const definition={name:'Test method',summary:'A reviewed method',instructions:'Check evidence.',user_roles:['admin'],agent_roles:['tool'],required_tools:[],script:'',exports:[],seconds:20,parameter_schema:{type:'object'}}
const settings={models:{understanding:'demo'},role_notes:{},intent_cards:[],multi_agent_enabled:true}
const skills={items:[{_id:'test-method',revision:2,enabled:false}],versions:[{_id:'v1',skill_id:'test-method',status:'draft',created_at:'2026-10-01',definition},{_id:'v2',skill_id:'test-method',status:'draft',created_at:'2026-10-02',definition:{...definition,name:'Second version'}}]}
function response(path:string):any {
  if(path==='/admin/v1/skills')return skills
  if(path==='/admin/v1/mcp')return {revision:1,policy:{mode:'selected',selected:[],services:[]},endpoints:[{ref:'approved',ready:true}],catalogs:[],recent:[]}
  if(path==='/admin/v1/agent-configuration')return {revision:1,active:{version:'baseline',settings},builtin:{version:'baseline'},model_choices:['demo'],versions:[]}
  if(path==='/v1/graph/catalog')return {entities:[],edges:[],revision:1,configured:true}
  if(path==='/v1/memories')return {items:[],enabled:true,revision:1}
  if(path==='/admin/v1/evaluations')return {summary:{run_count:0,human_label_count:0,grounding_supported_rate:null,grounding_checked_count:0,action_macro_f1:null,p95_elapsed_ms:null},items:[]}
  if(path==='/admin/v1/search')return {revision:1,policy:{providers:{bocha:{enabled:true,daily_limit:null}},order:['bocha'],timeout_seconds:10},providers:[{name:'bocha',credential_configured:true,requests_today:0}],recent:[]}
  return {items:[]}
}
let mounted:VueWrapper[]=[]
const StubDrawer=defineComponent({props:['modelValue','title'],emits:['update:modelValue'],template:'<div v-if="modelValue" class="test-drawer"><h2>{{title}}</h2><slot/><slot name="footer"/></div>'})
async function page(component:any){const w=mount(component,{props:{manage:true},global:{stubs:{ResourceDrawer:StubDrawer,MarkdownAnswer:true,RunDiagnostics:true}}});mounted.push(w);await flushPromises();return w}
function button(w:VueWrapper,text:string){return w.findAll('button').find(b=>b.text()===text)!}
beforeEach(()=>{vi.mocked(api).mockImplementation(async(path:any)=>structuredClone(response(path)));vi.mocked(post).mockResolvedValue({});vi.mocked(documentCatalog).mockResolvedValue({items:[],next_cursor:null} as any);vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as any);vi.stubGlobal('matchMedia',()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}));vi.stubGlobal('requestAnimationFrame',vi.fn(()=>1));vi.stubGlobal('cancelAnimationFrame',vi.fn())})
afterEach(()=>{for(const w of mounted)w.unmount();mounted=[];vi.clearAllMocks();vi.unstubAllGlobals()})
describe('management pages keep their existing contracts',()=>{
  for(const [name,component] of Object.entries({Skills,MCP,Config,Knowledge,Wiki,Memory,Users,Evaluation,Comparison,Operations,Graph}))it(`mounts ${name} with API data without setup errors`,async()=>{const w=await page(component);expect(w.text().length).toBeGreaterThan(20)})
  it('does not carry a skill review acknowledgement to a different version',async()=>{const w=await page(Skills);await button(w,'详情与版本').trigger('click');await w.find('.test-drawer input[type=checkbox]').setValue(true);await w.find('.test-drawer input:not([type=checkbox])').setValue('Reviewed version one');expect(button(w,'发布所选版本').attributes('disabled')).toBeUndefined();await w.find('.test-drawer select').setValue('v2');expect(button(w,'发布所选版本').attributes('disabled')).toBeDefined();expect((w.find('.test-drawer input:not([type=checkbox])').element as HTMLInputElement).value).toBe('')})
  it('keeps a skill draft when the server rejects a stale revision',async()=>{vi.mocked(post).mockRejectedValue(new Error('资源已更新'));const w=await page(Skills);await button(w,'编辑').trigger('click');await w.find('textarea').setValue('My unsaved summary');await w.find('form').trigger('submit');await flushPromises();expect(w.find('.test-drawer').exists()).toBe(true);expect((w.find('textarea').element as HTMLTextAreaElement).value).toBe('My unsaved summary');expect(w.text()).toContain('资源已更新')})
  it('opening MCP configuration never probes a connection or publishes',async()=>{const w=await page(MCP);await button(w,'＋ 添加服务').trigger('click');expect(post).not.toHaveBeenCalled()})
  it('keeps the published search policy intact when cancelling an edit',async()=>{const w=await page(Operations);await button(w,'编辑搜索配置').trigger('click');await w.find('.test-drawer input[type=checkbox]').setValue(false);expect(w.find('.resource-grid').text()).toContain('已启用');w.findComponent(StubDrawer).vm.$emit('update:modelValue',false);await nextTick();await button(w,'编辑搜索配置').trigger('click');expect((w.find('.test-drawer input[type=checkbox]').element as HTMLInputElement).checked).toBe(true);expect(post).not.toHaveBeenCalled()})
  it('retains MCP configuration after a failed removal',async()=>{vi.mocked(api).mockImplementation(async(path:any)=>path==='/admin/v1/mcp'?{...response(path),policy:{mode:'selected',selected:['probe'],services:[{id:'probe',label:'Probe',endpoint_ref:'approved',enabled:false,user_roles:['admin'],agent_roles:['tool'],allowed_tools:[],timeout_seconds:20}]}}:response(path));vi.mocked(post).mockRejectedValue(new Error('Version conflict'));const w=await page(MCP);await button(w,'配置与工具').trigger('click');await button(w,'确认移除并保存').trigger('click');await flushPromises();expect((w.find('.test-drawer input').element as HTMLInputElement).value).toBe('probe');expect(w.text()).toContain('Version conflict')})
  it('filters the complete document collection before pagination',async()=>{vi.mocked(documentCatalog).mockResolvedValue({items:Array.from({length:31},(_,i)=>({id:String(i),title:i===30?'needle.md':`document-${i}.md`,path:`${i}`,active_version:'v1'}))} as any);const w=await page(Knowledge);expect(w.findAll('.document-row')).toHaveLength(25);await w.find('input[type=search]').setValue('needle');expect(w.findAll('.document-row')).toHaveLength(1);expect(w.find('.document-row').text()).toContain('needle.md')})
})
describe('editor lifecycle',()=>{
  it('captures the incoming snapshot after opening and guards edited data',async()=>{const w=mount(Drawer,{props:{modelValue:false,title:'Edit',snapshot:{name:''}}});mounted.push(w);await w.setProps({modelValue:true,snapshot:{name:'server value'}});expect(await confirmNavigation()).toBe(true);expect(ElMessageBox.confirm).not.toHaveBeenCalled();await w.setProps({snapshot:{name:'changed'}});vi.mocked(ElMessageBox.confirm).mockRejectedValueOnce('cancel');expect(await confirmNavigation()).toBe(false);expect(w.emitted('update:modelValue')).toBeUndefined()})
  it('prevents leaving a saving editor and unregisters after unmount',async()=>{const w=mount(Drawer,{props:{modelValue:true,title:'Edit',busy:true}});expect(await confirmNavigation()).toBe(false);w.unmount();expect(await confirmNavigation()).toBe(true)})
})
describe('graph navigation',()=>{
  it('uses finite stable positions in reduced motion and supports keyboard details',async()=>{const w=mount(GraphCanvas,{props:{nodes:[{id:'a',name:'Process A',kind:'process'},{id:'b',name:'Device B',kind:'equipment'}],edges:[{id:'e',subject_id:'a',object_id:'b',relation:'references'}],types:{process:'Process',equipment:'Equipment'},relations:{references:'References'}}});mounted.push(w);await nextTick();expect(w.html()).not.toContain('NaN');expect(requestAnimationFrame).not.toHaveBeenCalled();await w.find('.graph-node').trigger('keydown',{key:'Enter'});expect(w.emitted('select')).toEqual([['a']]);await button(w,'适应画布').trigger('click');expect(w.html()).not.toContain('NaN')})
})
