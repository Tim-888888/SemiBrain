<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { GRAPH_NODE_RADIUS, separateNodes, stepGraph, type GraphPositions } from './graph-physics'
interface Node { id:string; name:string; kind:string }
interface Edge { id:string; subject_id:string; object_id:string; relation:string }
const props=defineProps<{nodes:Node[];edges:Edge[];types:Record<string,string>;relations:Record<string,string>}>()
const emit=defineEmits<{select:[id:string]}>()
const svg=ref<SVGSVGElement|null>(null), hovered=ref(''), selected=ref(''), zoom=ref(1), pan=ref({x:0,y:0})
const positions=ref<GraphPositions>({})
const colors:Record<string,string>={process:'#168575',equipment:'#5381bd',defect:'#c97c44',product:'#886eab',chamber:'#697ca2',concept:'#65846a',alarm:'#c66767',sop:'#ae9155',action:'#58919b'}
const kinds=computed(()=>[...new Set(props.nodes.map(n=>n.kind))])
const highlight=computed(()=>{const id=hovered.value || selected.value;const ids=new Set([id]);for(const edge of props.edges){if(edge.subject_id===id)ids.add(edge.object_id);if(edge.object_id===id)ids.add(edge.subject_id)}return ids})
const focus=computed(()=>hovered.value || selected.value)
const markerId='sb-graph-arrow-'+crypto.randomUUID()
let frame=0, tick=0, disposed=false, reduced=false, anchor=''
const MAX_TICKS=180
let drag:{id:string;start:{x:number;y:number};origin:{x:number;y:number};moved:boolean;pointer:number}|null=null
function point(id:string){return positions.value[id] || {x:420,y:220,vx:0,vy:0}}
function stop(){cancelAnimationFrame(frame);frame=0}
function step(){const speed=stepGraph(positions.value,props.edges,Math.max(.025,1-tick/MAX_TICKS),anchor);tick++;return speed}
function animate(){
  frame=0
  if(disposed)return
  const speed=step()
  if(tick<MAX_TICKS && (tick<45 || speed>.035))frame=requestAnimationFrame(animate)
}
function wake(){
  tick=0
  if(reduced){separateNodes(positions.value,anchor);return}
  if(!frame && !disposed)frame=requestAnimationFrame(animate)
}
function layout(){
  stop();drag=null;anchor=''
  positions.value=Object.fromEntries(props.nodes.map((n,i)=>{const a=2*Math.PI*i/Math.max(1,props.nodes.length);return [n.id,{x:420+Math.cos(a)*240,y:210+Math.sin(a)*145,vx:0,vy:0}]}))
  tick=0;zoom.value=1;pan.value={x:0,y:0};selected.value='';hovered.value=''
  if(reduced){for(let i=0;i<MAX_TICKS;i++)step()}else wake()
}
function fit(){stop();const pts=Object.values(positions.value);if(!pts.length)return;const minX=Math.min(...pts.map(p=>p.x))-70,maxX=Math.max(...pts.map(p=>p.x))+70,minY=Math.min(...pts.map(p=>p.y))-35,maxY=Math.max(...pts.map(p=>p.y))+55;zoom.value=Math.max(.35,Math.min(2,Math.min(800/(maxX-minX),400/(maxY-minY))));pan.value={x:420-(minX+maxX)/2*zoom.value,y:220-(minY+maxY)/2*zoom.value}}
function local(event:PointerEvent|WheelEvent){const matrix=svg.value?.getScreenCTM();if(!matrix)return {x:0,y:0};const p=new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());return {x:p.x,y:p.y}}
function down(event:PointerEvent,id=''){
  if(event.button!==0 || drag)return
  stop();svg.value?.focus();const p=local(event)
  drag={id,start:p,origin:id?{...point(id)}:{...pan.value},moved:false,pointer:event.pointerId}
  if(id){anchor=id;point(id).vx=0;point(id).vy=0}
  svg.value?.setPointerCapture(event.pointerId)
}
function move(event:PointerEvent){
  if(!drag || drag.pointer!==event.pointerId)return
  const p=local(event),dx=p.x-drag.start.x,dy=p.y-drag.start.y
  if(Math.hypot(dx,dy)>4)drag.moved=true
  if(drag.id){
    const n=positions.value[drag.id]
    if(n && drag.moved){
      n.x=drag.origin.x+dx/zoom.value;n.y=drag.origin.y+dy/zoom.value;n.vx=0;n.vy=0
      // Apply before painting, including fast pointer jumps onto another center.
      separateNodes(positions.value,drag.id);wake()
    }
  }else pan.value={x:drag.origin.x+dx,y:drag.origin.y+dy}
}
function up(event:PointerEvent,cancelled=false){
  if(!drag || drag.pointer!==event.pointerId)return
  const finished=drag;drag=null
  if(svg.value?.hasPointerCapture(event.pointerId))svg.value.releasePointerCapture(event.pointerId)
  if(finished.id && finished.moved)wake()
  else if(finished.id && !cancelled)choose(finished.id)
}
function choose(id:string){selected.value=id;emit('select',id)}
function scale(factor:number,at={x:420,y:220}){const before=zoom.value,after=Math.min(3,Math.max(.35,before*factor));pan.value={x:at.x-(at.x-pan.value.x)*after/before,y:at.y-(at.y-pan.value.y)*after/before};zoom.value=after}
function wheel(event:WheelEvent){if(document.activeElement!==svg.value && !event.ctrlKey && !event.metaKey)return;event.preventDefault();scale(event.deltaY<0?1.1:1/1.1,local(event))}
function key(event:KeyboardEvent){if(event.key==='+' || event.key==='='){event.preventDefault();scale(1.2)}else if(event.key==='-'){event.preventDefault();scale(1/1.2)}else if(event.key==='0'){event.preventDefault();fit()}}
watch(()=>JSON.stringify([props.nodes.map(n=>n.id),props.edges.map(e=>[e.subject_id,e.object_id])]),()=>layout())
let media:MediaQueryList|undefined
function motion(){reduced=!!media?.matches;if(reduced){stop();separateNodes(positions.value,anchor)}}
onMounted(()=>{media=matchMedia('(prefers-reduced-motion: reduce)');reduced=media.matches;media.addEventListener('change',motion);layout()})
onUnmounted(()=>{disposed=true;stop();media?.removeEventListener('change',motion)})
</script>
<template>
<div class="interactive-graph">
  <div class="graph-toolbar"><div class="graph-legend"><span v-for="kind in kinds" :key="kind"><i :style="{background:colors[kind] || '#65846a'}"/>{{types[kind] || kind}}</span></div><div class="row-actions"><button class="secondary" @click="scale(1/1.2)" aria-label="缩小图谱">−</button><span class="small muted">{{Math.round(zoom*100)}}%</span><button class="secondary" @click="scale(1.2)" aria-label="放大图谱">＋</button><button class="secondary" @click="fit">适应画布</button><button class="secondary" @click="layout">重新布局</button></div></div>
  <svg ref="svg" viewBox="0 0 840 440" role="group" tabindex="0" aria-label="实体关系图：可拖动节点，拖空白平移；聚焦后滚轮缩放，按加减键缩放、0 适应画布" @pointerdown="down($event)" @pointermove="move" @pointerup="up" @pointercancel="up($event,true)" @lostpointercapture="up($event,true)" @wheel="wheel" @keydown="key">
    <defs><marker :id="markerId" viewBox="0 0 10 10" refX="21" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#92afa5"/></marker></defs>
    <g :transform="`translate(${pan.x} ${pan.y}) scale(${zoom})`">
      <g v-for="edge in edges" :key="edge.id" :opacity="focus && edge.subject_id!==focus && edge.object_id!==focus ? .18:1"><line :x1="point(edge.subject_id).x" :y1="point(edge.subject_id).y" :x2="point(edge.object_id).x" :y2="point(edge.object_id).y" stroke="#aac2b9" stroke-width="1.6" :marker-end="`url(#${markerId})`"/><text v-if="edge.subject_id===focus || edge.object_id===focus || zoom>1.5" :x="(point(edge.subject_id).x+point(edge.object_id).x)/2" :y="(point(edge.subject_id).y+point(edge.object_id).y)/2-7" class="edge-label">{{relations[edge.relation] || edge.relation}}</text></g>
      <g v-for="node in nodes" :key="node.id" role="button" tabindex="0" :aria-label="`${node.name} · ${types[node.kind] || node.kind}，查看来源`" :transform="`translate(${point(node.id).x} ${point(node.id).y})`" :opacity="focus && !highlight.has(node.id) ? .28:1" class="graph-node" @pointerdown.stop="down($event,node.id)" @pointerenter="hovered=node.id" @pointerleave="hovered=''" @keydown.enter.stop="choose(node.id)" @keydown.space.prevent.stop="choose(node.id)">
        <circle :r="GRAPH_NODE_RADIUS" :fill="colors[node.kind] || '#65846a'" fill-opacity=".12"/><circle r="13" :fill="colors[node.kind] || '#65846a'" :stroke="selected===node.id?'#223a31':'#fff'" stroke-width="2"/><text y="37" text-anchor="middle">{{node.name.length>18?node.name.slice(0,18)+'…':node.name}}</text><title>{{node.name}}</title>
      </g>
    </g>
  </svg>
  <div class="graph-caption"><span>拖动节点会推开邻近节点 · 拖空白平移 · 聚焦后滚轮缩放 · 点击查看来源</span><details><summary>实体列表（{{nodes.length}}）</summary><button v-for="node in nodes" :key="node.id" class="text-button" @click="choose(node.id)">{{node.name}}</button></details></div>
</div>
</template>
<style scoped>
.interactive-graph{border:1px solid var(--border);border-radius:12px;overflow:hidden;background:#fff;margin:20px 0}.graph-toolbar{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border);flex-wrap:wrap}.graph-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#6b7c76}.graph-legend span{display:flex;align-items:center;gap:6px}.graph-legend i{width:8px;height:8px;border-radius:50%}svg{width:100%;height:clamp(280px,48vh,540px);display:block;background:radial-gradient(#dfe7e3 .8px,transparent .8px);background-size:20px 20px;cursor:grab;touch-action:none}svg:active{cursor:grabbing}.graph-node{cursor:grab;outline:none}.graph-node:focus-visible circle:first-child{stroke:#193d33;stroke-width:2}.graph-node text{font-size:12px;fill:#3b5148;paint-order:stroke;stroke:#fff;stroke-width:4px;pointer-events:none}.edge-label{font-size:10px;fill:#668275;text-anchor:middle;paint-order:stroke;stroke:#fff;stroke-width:3px}.graph-caption{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:10px 18px;border-top:1px solid var(--border);color:#6b7c76;font-size:11px}.graph-caption details{max-width:50%}.graph-caption summary{cursor:pointer}
</style>
