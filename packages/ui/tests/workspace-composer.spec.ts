import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import Workspace from '../src/Workspace.vue'
import { api, post } from '../src/api'

vi.mock('../src/api',()=>({api:vi.fn(),post:vi.fn(),setCsrf:vi.fn(),setCacheUser:vi.fn(),invalidateReadCache:vi.fn()}))
vi.mock('../src/document-catalog',()=>({documentCatalog:async()=>({items:[{id:'doc-1',title:'CP 测试资料',active_version:'v1',asset_id:'asset-1'}]})}))
vi.mock('element-plus/es/components/drawer/style/css',()=>({}))
vi.mock('element-plus/es/components/message-box/style/css',()=>({}))
vi.mock('../src/ConversationHistoryItem.vue',()=>({default:defineComponent({template:'<div/>'})}))
vi.mock('element-plus',()=>({ElMessageBox:{confirm:vi.fn()},ElConfigProvider:defineComponent({template:'<slot/>'}),ElDrawer:defineComponent({props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'})}))
let wrapper: ReturnType<typeof mount> | undefined, stream: EventTarget
const open = async (selector:string) => {const details=wrapper!.find(selector);(details.element as HTMLDetailsElement).open=true;await details.trigger('toggle');await flushPromises()}
beforeEach(()=>{
  localStorage.clear();sessionStorage.clear()
  vi.stubGlobal('matchMedia',()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}))
  vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}})
  vi.stubGlobal('requestAnimationFrame',()=>1);vi.stubGlobal('cancelAnimationFrame',vi.fn())
  vi.stubGlobal('EventSource',class extends EventTarget {constructor(){super();stream=this}close(){}})
  vi.mocked(api).mockImplementation(async(path:string)=>{
    if(path==='/v1/auth/me')return {csrf:'test',user:{id:'user-1',username:'tester',role:'user',enabled:true,revision:1}}
    if(path==='/v1/capabilities')return {multi_agent:true}
    if(path.startsWith('/v1/mcp/services'))return {services:[{id:'mcp-1',label:'量测工具',tool_count:1}]}
    if(path==='/v1/attachments/images')return {asset_id:'image-1'}
    return {items:[]}
  })
  vi.mocked(post).mockImplementation(async(path:string)=>path==='/v1/conversations'?{id:'chat-1',revision:0}:{turn_id:'turn-1',run_id:'run-1',input_revision:1})
})
afterEach(()=>{wrapper?.unmount();wrapper=undefined;vi.clearAllMocks();vi.unstubAllGlobals();document.body.innerHTML=''})

describe('mobile composer retains the existing request contract',()=>{
  it('keeps selected scopes after dismissal and sends the same options, then renders stream completion',async()=>{
    wrapper=mount(Workspace,{props:{audience:'user'},attachTo:document.body});await flushPromises()
    expect(wrapper.find('.workspace').classes()).toContain('mobile-chat')
    expect(wrapper.findAll('[aria-label="问答模式"]')).toHaveLength(1)
    await wrapper.find('select[aria-label="问答模式"]').setValue('investigation')
    await wrapper.findAll('.web-toggle input')[0].setValue(true)
    await wrapper.findAll('.web-toggle input')[1].setValue(true)
    await open('.mcp-select');await wrapper.find('.mcp-select input').setValue(false)
    await wrapper.find('.mcp-choice input').setValue(true)
    await open('.source-select:not(.mcp-select)');await wrapper.find('.source-select:not(.mcp-select) input').setValue(true)
    document.body.dispatchEvent(new Event('pointerdown',{bubbles:true}));await flushPromises()
    expect(wrapper.find('.source-select:not(.mcp-select)').attributes('open')).toBeUndefined()
    expect(wrapper.find('.source-select:not(.mcp-select) summary').text()).toContain('1')
    expect(wrapper.find('.mcp-select summary').text()).toContain('1')
    const input=wrapper.find('textarea')
    await input.setValue('比较 CP 和 FT，保留原文证据')
    await input.trigger('keydown',{key:'Enter',isComposing:true});expect(post).not.toHaveBeenCalled()
    await input.trigger('keydown',{key:'Enter',shiftKey:true});expect(post).not.toHaveBeenCalled()
    await wrapper.find('.send-button').trigger('click');await flushPromises()
    expect(post).toHaveBeenLastCalledWith('/v1/conversations/chat-1/messages',expect.objectContaining({
      text:'比较 CP 和 FT，保留原文证据',mode:'investigation',investigation_strategy:'multi_agent',allow_web:true,mcp_service_ids:['mcp-1'],resource_restrictions:['doc-1'],attachment_refs:[],
    }),expect.any(String))
    expect((input.element as HTMLTextAreaElement).value).toBe('');expect(input.attributes('disabled')).toBeDefined()
    stream.dispatchEvent(new MessageEvent('snapshot',{data:JSON.stringify({run_id:'run-1',status:'running',sequence:1,body_markdown:'正在输出',citations:[]})}));await flushPromises()
    expect(wrapper.text()).toContain('正在输出')
    stream.dispatchEvent(new MessageEvent('snapshot',{data:JSON.stringify({run_id:'run-1',status:'succeeded',sequence:2,body_markdown:'## 完成\n\nCP 与 FT 对比结果',citations:[]})}));await flushPromises()
    expect(wrapper.text()).toContain('CP 与 FT 对比结果');expect(input.attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('.report-export')).toHaveLength(1)
  })
  it('keeps image preview/removal and attachment references in the submitted request',async()=>{
    wrapper=mount(Workspace,{props:{audience:'user'},attachTo:document.body});await flushPromises()
    await wrapper.find('select').setValue('investigation');await wrapper.findAll('.web-toggle input')[0].setValue(true)
    const file=new File(['image fixture'],'probe.png',{type:'image/png'})
    const picker=wrapper.find('input[type="file"]')
    Object.defineProperty(picker.element,'files',{configurable:true,value:[file]})
    await picker.trigger('change')
    await vi.waitFor(()=>expect(wrapper!.find('.composer-image img').exists()).toBe(true))
    expect(wrapper.find('.composer-image img').attributes('alt')).toBe('probe.png')
    await wrapper.find('.composer-image button').trigger('click');expect(wrapper.find('.composer-images').exists()).toBe(false)
    await picker.trigger('change');await vi.waitFor(()=>expect(wrapper!.find('.composer-image img').exists()).toBe(true))
    await wrapper.find('textarea').setValue('检查附件中的测试图')
    await wrapper.find('.send-button').trigger('click');await flushPromises()
    expect(post).toHaveBeenLastCalledWith(expect.stringContaining('/messages'),expect.objectContaining({attachment_refs:['image-1']}),expect.any(String))
  })
})
