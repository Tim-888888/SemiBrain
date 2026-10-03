import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import GraphCanvas from '../src/GraphCanvas.vue'
import { GRAPH_MIN_DISTANCE } from '../src/graph-physics'

let wrapper:VueWrapper, frames:Map<number,FrameRequestCallback>, serial:number, reduced=false
const props={nodes:[{id:'a',name:'First',kind:'process'},{id:'b',name:'Second',kind:'process'},{id:'c',name:'Third',kind:'process'}],edges:[{id:'e',subject_id:'a',object_id:'b',relation:'precedes'}],types:{process:'Process'},relations:{precedes:'Before'}}
beforeEach(()=>{
  frames=new Map();serial=0;reduced=false
  vi.stubGlobal('matchMedia',()=>({matches:reduced,addEventListener:vi.fn(),removeEventListener:vi.fn()}))
  vi.stubGlobal('requestAnimationFrame',(cb:FrameRequestCallback)=>{frames.set(++serial,cb);return serial})
  vi.stubGlobal('cancelAnimationFrame',(id:number)=>frames.delete(id))
  vi.stubGlobal('DOMPoint',class{constructor(public x:number,public y:number){}matrixTransform(){return this}})
})
afterEach(()=>{wrapper?.unmount();vi.unstubAllGlobals()})
async function create(){
  wrapper=mount(GraphCanvas,{props});await nextTick()
  const svg=wrapper.find('svg').element as any
  svg.getScreenCTM=()=>({inverse:()=>({})})
  svg.setPointerCapture=vi.fn();svg.hasPointerCapture=()=>true;svg.releasePointerCapture=vi.fn()
}
async function advance(count:number){for(let i=0;i<count;i++){const batch=[...frames];frames.clear();for(const [,cb] of batch)cb(i*16);await nextTick()}}
function position(index:number){const v=wrapper.findAll('.graph-node')[index].attributes('transform').match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/g)!.map(Number);return {x:v[0],y:v[1]}}
async function pointer(element:Element,type:string,options:MouseEventInit={}){
  const event=new MouseEvent(type,{bubbles:true,...options});Object.defineProperty(event,'pointerId',{value:7})
  element.dispatchEvent(event);await nextTick()
}
async function start(index:number,x:number,y:number){await pointer(wrapper.findAll('.graph-node')[index].element,'pointerdown',{button:0,clientX:x,clientY:y})}
async function move(x:number,y:number){await pointer(wrapper.find('svg').element,'pointermove',{clientX:x,clientY:y})}
async function end(type='pointerup'){await pointer(wrapper.find('svg').element,type)}
function gap(){const a=position(0),b=position(1);return Math.hypot(a.x-b.x,a.y-b.y)}
describe('graph pointer and simulation lifecycle',()=>{
  it('wakes a settled graph, keeps the dragged center fixed and stops again after release',async()=>{
    await create();await advance(200);expect(frames.size).toBe(0)
    const a=position(0),b=position(1)
    await start(0,a.x,a.y);await move(b.x,b.y)
    expect(position(0).x).toBeCloseTo(b.x);expect(position(0).y).toBeCloseTo(b.y)
    expect(gap()).toBeGreaterThanOrEqual(GRAPH_MIN_DISTANCE-1e-6);expect(frames.size).toBe(1)
    await advance(6);expect(position(0)).toEqual(b);expect(gap()).toBeGreaterThanOrEqual(GRAPH_MIN_DISTANCE-1e-6)
    await end();await advance(200);expect(frames.size).toBe(0);expect(wrapper.emitted('select')).toBeUndefined()
    const stable=wrapper.html();await advance(20);expect(wrapper.html()).toBe(stable)
    // A second drag frees the previous anchor and pushes it out of the way.
    const currentA=position(0),currentB=position(1)
    await start(1,currentB.x,currentB.y);await move(currentA.x,currentA.y);await end();await advance(200)
    expect(position(1)).toEqual(currentA);expect(gap()).toBeGreaterThanOrEqual(GRAPH_MIN_DISTANCE-1e-6)
  })
  it('uses graph-space distance after zooming and still allows ordinary clicks',async()=>{
    await create();await advance(200)
    await wrapper.find('[aria-label="放大图谱"]').trigger('click')
    const a=position(0),b=position(1),screen=(p:{x:number;y:number})=>({x:420+(p.x-420)*1.2,y:220+(p.y-220)*1.2})
    const from=screen(a),to=screen(b)
    await start(0,from.x,from.y);await move(to.x,to.y);await end()
    expect(position(0).x).toBeCloseTo(b.x);expect(position(0).y).toBeCloseTo(b.y);expect(gap()).toBeGreaterThanOrEqual(GRAPH_MIN_DISTANCE-1e-6)
    await start(0,to.x,to.y);await end();expect(wrapper.emitted('select')).toEqual([['a']])
  })
  it('separates nodes in reduced motion without scheduling continuous animation',async()=>{
    reduced=true;await create();const a=position(0),b=position(1)
    await start(0,a.x,a.y);await move(b.x,b.y);await end()
    expect(frames.size).toBe(0);expect(gap()).toBeGreaterThanOrEqual(GRAPH_MIN_DISTANCE-1e-6)
  })
  it('cancels dragging without opening details and removes animation on unmount',async()=>{
    await create();await advance(200);const a=position(0),b=position(1)
    await start(0,a.x,a.y);await move(b.x,b.y);await end('pointercancel')
    expect(wrapper.emitted('select')).toBeUndefined();expect(frames.size).toBe(1)
    wrapper.unmount();expect(frames.size).toBe(0)
  })
  it('panning does not change node coordinates or restart the simulation',async()=>{
    await create();await advance(200);const a=position(0)
    await pointer(wrapper.find('svg').element,'pointerdown',{button:0,clientX:10,clientY:10})
    await move(60,80);await end();expect(position(0)).toEqual(a);expect(frames.size).toBe(0)
  })
})
