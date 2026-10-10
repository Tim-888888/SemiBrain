import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {mount,flushPromises,type VueWrapper} from '@vue/test-utils'
import {defineComponent} from 'vue'
import MemoryPanel from '../src/MemoryPanel.vue'
import {api,post} from '../src/api'
vi.mock('../src/api',()=>({api:vi.fn(),post:vi.fn()}))
vi.mock('../src/page-filters',()=>({rememberFilters:vi.fn()}))
vi.mock('../src/ResourceDrawer.vue',()=>({default:defineComponent({template:'<slot/>'})}))
const Drawer=defineComponent({props:['modelValue'],template:'<section v-if="modelValue"><slot/><slot name="footer"/></section>'})
let wrapper:VueWrapper|undefined
let data:any
const suggestion={_id:'candidate',kind:'preference',content:'先给结论',quote:'以后先给结论',state:'active'}
async function open(){wrapper=mount(MemoryPanel,{global:{stubs:{ResourceDrawer:Drawer,RefreshDataButton:true}}});await flushPromises();return wrapper}
function button(text:string){return wrapper!.findAll('button').find(b=>b.text()===text)!}
beforeEach(()=>{data={items:[],candidates:[suggestion],enabled:true,auto_extract:true,revision:3};vi.mocked(api).mockImplementation(async()=>structuredClone(data));vi.mocked(post).mockResolvedValue({})})
afterEach(()=>{wrapper?.unmount();vi.clearAllMocks()})
it('keeps suggestions pending until the user reviews and confirms',async()=>{
 const w=await open();expect(w.text()).toContain('尚未用于回答');expect(post).not.toHaveBeenCalled()
 await button('查看并确认').trigger('click');expect(button('确认保存').attributes('disabled')).toBeDefined()
 await w.find('textarea').setValue('先给结论，再列依据');await w.find('#memory-editor input[type=checkbox]').setValue(true)
 await w.find('form').trigger('submit');await flushPromises()
 expect(post).toHaveBeenCalledWith('/v1/memories/candidates/candidate/confirm',expect.objectContaining({content:'先给结论，再列依据',confirmed:true}))
})
it('uses the existing master switch and disables candidate confirmation when off',async()=>{
 const w=await open();data.enabled=false;await button('关闭记忆').trigger('click');await flushPromises()
 expect(post).toHaveBeenCalledWith('/v1/memories/settings',expect.objectContaining({enabled:false,expected_revision:3}))
 expect(w.text()).toContain('开启记忆');expect(button('查看并确认').attributes('disabled')).toBeDefined()
 expect(w.find('.memory-automatic input').attributes('disabled')).toBeDefined()
})
it('changes extraction separately without deleting or disabling saved memory',async()=>{
 const w=await open();await w.find('.memory-automatic input').setValue(false);await flushPromises()
 expect(post).toHaveBeenCalledTimes(1)
 expect(post).toHaveBeenCalledWith('/v1/memories/settings',expect.objectContaining({enabled:true,auto_extract:false}))
})
it('allows dismissal without saving any active memory',async()=>{
 await open();await button('忽略').trigger('click');await flushPromises()
 expect(post).toHaveBeenCalledTimes(1);expect(vi.mocked(post).mock.calls[0][0]).toBe('/v1/memories/candidates/candidate/dismiss')
})
it('keeps manual entries available and defaults legacy accounts to manual mode',async()=>{
 data={items:[],enabled:true,revision:0};const w=await open()
 expect((w.find('.memory-automatic input').element as HTMLInputElement).checked).toBe(false)
 await button('＋ 添加记忆').trigger('click');await w.find('textarea').setValue('偏好简短回答')
 await w.find('#memory-editor input[type=checkbox]').setValue(true);await w.find('form').trigger('submit');await flushPromises()
 expect(post).toHaveBeenCalledWith('/v1/memories',expect.objectContaining({content:'偏好简短回答',confirmed:true}))
})
