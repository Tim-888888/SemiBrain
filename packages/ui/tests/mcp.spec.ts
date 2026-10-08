import { describe, expect, it, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { parseMcpImport, connectionHeaders, emptyConnection } from '../src/mcp-config'
import Selector from '../src/MCPSelector.vue'
import Panel from '../src/MCPPanel.vue'
import { api, post } from '../src/api'
vi.mock('../src/api',()=>({api:vi.fn(),post:vi.fn()}))
vi.mock('element-plus/es/components/drawer/style/css',()=>({}))
vi.mock('element-plus/es/components/message-box/style/css',()=>({}))
vi.mock('element-plus',()=>({ElMessageBox:{confirm:vi.fn()},ElConfigProvider:defineComponent({template:'<slot/>'}),ElDrawer:defineComponent({props:['modelValue'],template:'<div v-if="modelValue"><slot/><slot name="footer"/></div>'})}))
afterEach(()=>vi.clearAllMocks())
describe('remote MCP config import',()=>{
  it('imports multiple services for an explicit choice, preserving headers and protocols',()=>{
    const result=parseMcpImport(JSON.stringify({mcpServers:{one:{url:'https://example.com/mcp',type:'http',headers:{Authorization:'Bearer test-token'}},two:{url:'https://other.example/sse'}}}))
    expect(result).toHaveLength(2);expect(result[0].connection.transport).toBe('streamable_http');expect(result[1].connection.transport).toBe('sse')
    expect(connectionHeaders(result[0].connection)).toEqual({Authorization:'Bearer test-token'})
  })
  it.each([{command:'npx',args:['server']},{url:'http://localhost/mcp'},{url:'https://host/mcp?token=secret'},{url:'https://host/mcp',type:'stdio'},{url:'https://host/mcp',headers:{key:123}}])('rejects unsupported configs without executing or silently dropping them',config=>{
    expect(()=>parseMcpImport(JSON.stringify(config))).toThrow()
  })
  it('distinguishes preserving stored credentials from clearing authentication',()=>{
    const draft=emptyConnection();draft.auth_type='bearer';expect(connectionHeaders(draft)).toBeNull()
    draft.token='test';expect(connectionHeaders(draft)).toEqual({Authorization:'Bearer test'})
    draft.auth_type='none';expect(connectionHeaders(draft)).toEqual({})
  })
})
describe('task MCP scope',()=>{
  it('turning off inheritance explicitly disables MCP and selecting a service narrows scope',async()=>{
    vi.mocked(api).mockResolvedValue({services:[{id:'a',label:'Alpha',tool_count:1},{id:'b',label:'Beta',tool_count:1}]})
    const w=mount(Selector,{props:{modelValue:null,multiAgent:false}})
    const details=w.find('details').element as HTMLDetailsElement;details.open=true;await w.find('details').trigger('toggle');await flushPromises()
    await w.findAll('input')[0].setValue(false)
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([[]])
    await w.findAll('input')[2].setValue(false)
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([['a']]);w.unmount()
  })
  it('locks external MCP for selected-document tasks',()=>{
    const w=mount(Selector,{props:{modelValue:null,multiAgent:false,documentOnly:true}})
    expect(w.find('input').attributes()).toHaveProperty('disabled');expect(w.text()).toContain('不使用外部 MCP');w.unmount()
  })
})
describe('MCP management',()=>{
  it('allows adding remote services without deployment-registered endpoints',async()=>{
    vi.mocked(api).mockResolvedValue({revision:1,policy:{mode:'none',selected:[],services:[]},endpoints:[],catalogs:[],recent:[],web_connections_ready:true})
    const w=mount(Panel);await flushPromises()
    const add=w.findAll('button').find(b=>b.text().includes('添加服务'))!
    expect(add.attributes('disabled')).toBeUndefined();await add.trigger('click')
    expect(w.find('input[type=url]').exists()).toBe(true);expect(w.text()).toContain('从 JSON 配置导入')
    expect(post).not.toHaveBeenCalled();w.unmount()
  })
})
