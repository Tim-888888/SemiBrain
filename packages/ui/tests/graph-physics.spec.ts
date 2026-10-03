import { describe, expect, it } from 'vitest'
import { GRAPH_MIN_DISTANCE, separateNodes, stepGraph, type GraphPositions } from '../src/graph-physics'

const point=(x:number,y:number)=>({x,y,vx:0,vy:0})
function separated(p:GraphPositions){
  const nodes=Object.values(p)
  for(let i=0;i<nodes.length;i++){
    expect(Number.isFinite(nodes[i].x) && Number.isFinite(nodes[i].y)).toBe(true)
    for(let j=i+1;j<nodes.length;j++)expect(Math.hypot(nodes[i].x-nodes[j].x,nodes[i].y-nodes[j].y)).toBeGreaterThanOrEqual(GRAPH_MIN_DISTANCE-1e-6)
  }
}
describe('graph dragging physics',()=>{
  it('pushes neighbors before contact while the grabbed node stays under the pointer',()=>{
    const p={grabbed:point(420,220),neighbor:point(500,220)}
    stepGraph(p,[],1,'grabbed')
    expect(p.grabbed).toEqual(point(420,220));expect(p.neighbor.x).toBeGreaterThan(500)
  })
  it('separates exactly coincident centers even without simulation heat',()=>{
    const p={a:point(100,100),b:point(100,100)}
    separateNodes(p,'b');separated(p);expect(p.b).toEqual(point(100,100))
  })
  it('separates a full 30-node coincident cluster without moving the grabbed node',()=>{
    const p=Object.fromEntries(Array.from({length:30},(_,i)=>['node-'+i,point(420,220)]))
    separateNodes(p,'node-15');separated(p);expect(p['node-15']).toEqual(point(420,220))
  })
  it('keeps all nodes apart through repeated dragging, edges and cooldown',()=>{
    const p:GraphPositions={a:point(55,40),b:point(80,45),c:point(785,375),d:point(784,374)}
    const edges=[{subject_id:'a',object_id:'b'},{subject_id:'b',object_id:'c'}]
    for(const [moving,target] of [['a','b'],['b','c'],['c','a'],['d','b']]){
      p[moving]=point(p[target].x,p[target].y);const held={...p[moving]}
      separateNodes(p,moving);separated(p)
      for(let i=0;i<180;i++){stepGraph(p,edges,Math.max(.025,1-i/180),moving);separated(p)}
      expect(p[moving]).toEqual(held)
    }
  })
})
