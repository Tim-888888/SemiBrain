import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import GraphExplorer from '../src/GraphExplorer.vue'
const mock=vi.hoisted(()=>({post:vi.fn()}))
vi.mock('../src/api',()=>mock)
vi.mock('../src/ResourceDrawer.vue',()=>({default:{name:'ResourceDrawer',template:'<div><slot/></div>'}}))
const node=(id:string,hidden=1)=>({id,canonical_id:id,name:id,kind:'process',neighbor_count:2,hidden_count:hidden})
const result=(ids=['a','b'])=>({entities:ids.map(id=>node(id)),edges:[{id:'e',subject_id:'a',object_id:'b',relation:'precedes',quote:'actual source',source_title:'Source'}],meta:{total:5,returned:ids.length,remaining:5-ids.length,truncated:true}})
const options={props:{revision:1,types:{process:'工序'},relations:{precedes:'先于'}},global:{stubs:{GraphCanvas:{name:'GraphCanvas',props:['nodes','edges','layoutKey','expansionAnchor'],template:'<div class="canvas"/>'},ResourceDrawer:{name:'ResourceDrawer',template:'<div><slot/></div>'}}}}
beforeEach(()=>{mock.post.mockReset();mock.post.mockResolvedValue(result())})
describe('graph exploration flows',()=>{
 it('sends visible IDs for additive expansion and resets them for a new center',async()=>{
  const w=mount(GraphExplorer,options);await flushPromises()
  const canvas=w.findComponent({name:'GraphCanvas'})
  mock.post.mockResolvedValue(result(['a','b','c']))
  canvas.vm.$emit('expand','b');await flushPromises()
  expect(mock.post.mock.lastCall?.[1]).toMatchObject({mode:'ego',center:'b',visible_ids:['a','b']})
  expect(canvas.props('expansionAnchor')).toBe('b');expect(canvas.props('layoutKey')).toBe(1)
  canvas.vm.$emit('explore','c');await flushPromises()
  expect(mock.post.mock.lastCall?.[1]).toMatchObject({mode:'ego',center:'c',visible_ids:[]})
  expect(canvas.props('layoutKey')).toBe(2);w.unmount()
 })
 it('retains the last good graph on network errors and reports the failure',async()=>{
  const w=mount(GraphExplorer,options);await flushPromises()
  mock.post.mockRejectedValue(new Error('Network failed'))
  w.findComponent({name:'GraphCanvas'}).vm.$emit('expand','b');await flushPromises()
  expect(w.find('[role="alert"]').text()).toContain('Network failed')
  expect(w.findComponent({name:'GraphCanvas'}).props('nodes')).toHaveLength(2);w.unmount()
 })
 it('displays the source evidence and exits the enlarged canvas with Escape',async()=>{
  const w=mount(GraphExplorer,options);await flushPromises()
  w.findComponent({name:'GraphCanvas'}).vm.$emit('select','a');await flushPromises()
  expect(w.text()).toContain('actual source');expect(w.text()).toContain('Source')
  w.findComponent({name:'ResourceDrawer'}).vm.$emit('update:modelValue',false)
  await w.findAll('button').find(b=>b.text()==='全屏查看')!.trigger('click')
  expect(w.find('[role="dialog"]').exists()).toBe(true)
  await w.find('[role="dialog"]').trigger('keydown',{key:'Escape'})
  expect(w.find('[role="dialog"]').exists()).toBe(false);w.unmount()
 })
})
