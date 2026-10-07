import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import GraphExtractionPanel from '../src/GraphExtractionPanel.vue'
import { api, post } from '../src/api'
vi.mock('../src/api',()=>({api:vi.fn(),post:vi.fn()}))
vi.mock('../src/ResourceDrawer.vue',()=>({default:{props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'}}))
const policy={enabled:false,scope:'wiki',auto_accept:false,model:'deepseek-flash',instructions:''}
const fact={subject:{name:'alpha',kind:'process',aliases:[]},object:{name:'beta',kind:'equipment',aliases:[]},relation:'uses',quote:'alpha uses beta in this example'}
const row={id:'c',candidate:fact,title:'Example',reason:'Review required',source_active:true}
const drawer={props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'}
let wrapper:any
afterEach(()=>{wrapper?.unmount();vi.clearAllMocks()})
async function create(){
 vi.mocked(api).mockImplementation(async(path:string)=>{
  if(path.endsWith('/models'))return {models:['deepseek-flash']}
  if(path.endsWith('/candidates/c'))return {...row,source_text:fact.quote}
  if(path.includes('kind=candidates'))return {items:[row],next_cursor:null}
  return {policy:{...policy},items:[],revision:3,next_cursor:null}
 })
 vi.mocked(post).mockResolvedValue({})
 wrapper=mount(GraphExtractionPanel,{props:{open:false,documents:[{id:'wiki',kind:'wiki',title:'Wiki source'},{id:'doc',kind:'document',title:'Document source'}]},global:{stubs:{ResourceDrawer:drawer}}})
 await wrapper.setProps({open:true});await flushPromises()
}
async function click(text:string){await wrapper.findAll('button').find((b:any)=>b.text()===text)!.trigger('click');await flushPromises()}
describe('graph construction management',()=>{
 it('queues only the selected published Wiki and switches to progress',async()=>{
  await create();expect(wrapper.text()).toContain('Wiki source');expect(wrapper.text()).not.toContain('Document source')
  await wrapper.find('.document-choice input').setValue(true);await click('抽取所选资料')
  expect(post).toHaveBeenCalledWith('/admin/v1/graph/extraction/queue',expect.objectContaining({document_ids:['wiki'],expected_revision:3}))
  expect(wrapper.text()).toContain('已加入独立后台队列')
 })
 it('opens a reactive candidate, edits a detached copy and sends a reviewed correction',async()=>{
  await create();await click('待审核');await click('查看原文并审核')
  expect(wrapper.find('.source-text').text()).toBe(fact.quote)
  await wrapper.find('.entity-edit input').setValue('corrected alpha')
  const textareas=wrapper.findAll('textarea');await textareas[textareas.length-1].setValue('Reviewed against the original source')
  await click('批准关系')
  expect(fact.subject.name).toBe('alpha')
  expect(post).toHaveBeenCalledWith('/admin/v1/graph/extraction/review',expect.objectContaining({action:'approve',correction:expect.objectContaining({subject:expect.objectContaining({name:'corrected alpha'})})}))
 })
 it('saves explicit automatic publication policy without submitting hidden selections',async()=>{
  await create();await click('自动构建设置')
  const checks=wrapper.findAll('input[type=checkbox]');await checks[0].setValue(true);await checks[1].setValue(true)
  await click('保存自动构建设置')
  expect(post).toHaveBeenCalledWith('/admin/v1/graph/extraction/policy',expect.objectContaining({policy:expect.objectContaining({enabled:true,auto_accept:true,scope:'wiki'})}))
 })
})
