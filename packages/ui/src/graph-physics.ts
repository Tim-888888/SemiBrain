export interface GraphPosition { x: number; y: number; vx: number; vy: number }
export type GraphPositions = Record<string, GraphPosition>
export const GRAPH_NODE_RADIUS = 22
export const GRAPH_MIN_DISTANCE = GRAPH_NODE_RADIUS * 2 + 12

// Coincident centers need a deterministic direction, not division by zero.
function direction(a: GraphPosition, b: GraphPosition, index: number) {
  const dx = b.x - a.x, dy = b.y - a.y, distance = Math.hypot(dx, dy)
  const angle = index * 2.399963229728653
  return { x: distance > 1e-8 ? dx / distance : Math.cos(angle), y: distance > 1e-8 ? dy / distance : Math.sin(angle), distance }
}

export function separateNodes(positions: GraphPositions, pinned = '') {
  const entries = Object.entries(positions)
  // Projection is independent of simulation heat: even a cold graph cannot overlap.
  for (let pass = 0; pass < 48; pass++) {
    let overlap = false
    for (let i = 0; i < entries.length; i++) for (let j = i + 1; j < entries.length; j++) {
      const [idA, a] = entries[i], [idB, b] = entries[j]
      const d = direction(a, b, i * entries.length + j)
      if (d.distance >= GRAPH_MIN_DISTANCE) continue
      overlap = true
      const correction = GRAPH_MIN_DISTANCE - d.distance + .001
      const weightA = idA === pinned ? 0 : idB === pinned ? 1 : .5
      const weightB = 1 - weightA
      a.x -= d.x * correction * weightA; a.y -= d.y * correction * weightA
      b.x += d.x * correction * weightB; b.y += d.y * correction * weightB
    }
    if (!overlap) return
  }
  // Dense/coincident clusters can converge slowly. Bounded placement guarantees
  // separation without an endless simulation. The dragged node always stays put.
  const ordered = entries.slice().sort(([a], [b]) => a === pinned ? -1 : b === pinned ? 1 : 0)
  const placed: GraphPosition[] = []
  const free = (x: number, y: number) => placed.every(p => Math.hypot(p.x - x, p.y - y) >= GRAPH_MIN_DISTANCE)
  for (const [, p] of ordered) {
    if (!free(p.x, p.y)) {
      const x = p.x, y = p.y
      search: for (let ring = 1; ring <= entries.length + 1; ring++) {
        for (let slot = 0; slot < 24; slot++) {
          const angle = slot * Math.PI / 12, radius = ring * GRAPH_MIN_DISTANCE
          const candidateX = x + Math.cos(angle) * radius, candidateY = y + Math.sin(angle) * radius
          if (free(candidateX, candidateY)) { p.x = candidateX; p.y = candidateY; p.vx = 0; p.vy = 0; break search }
        }
      }
    }
    placed.push(p)
  }
}

export function stepGraph(positions: GraphPositions, edges: { subject_id: string; object_id: string }[], heat: number, pinned = '') {
  const entries = Object.entries(positions)
  for (let i = 0; i < entries.length; i++) for (let j = i + 1; j < entries.length; j++) {
    const [idA, a] = entries[i], [idB, b] = entries[j], d = direction(a, b, i * entries.length + j)
    // Soft local pressure begins before contact, so neighbors move before the
    // hard collision boundary is reached. No screen clamp can trap two nodes.
    const force = 2800 / Math.max(250, d.distance ** 2) * heat + Math.max(0, 110 - d.distance) * .045 * heat
    if (idA !== pinned) { a.vx -= d.x * force; a.vy -= d.y * force }
    if (idB !== pinned) { b.vx += d.x * force; b.vy += d.y * force }
  }
  for (const edge of edges) {
    const a = positions[edge.subject_id], b = positions[edge.object_id]
    if (!a || !b) continue
    const d = direction(a, b, 0), force = (d.distance - 145) * .018 * heat
    if (edge.subject_id !== pinned) { a.vx += d.x * force; a.vy += d.y * force }
    if (edge.object_id !== pinned) { b.vx -= d.x * force; b.vy -= d.y * force }
  }
  for (const [id, p] of entries) {
    if (id === pinned) { p.vx = 0; p.vy = 0; continue }
    p.vx = (p.vx + (420 - p.x) * .0018 * heat) * .78
    p.vy = (p.vy + (220 - p.y) * .002 * heat) * .78
    const speed = Math.hypot(p.vx, p.vy)
    if (speed > 12) { p.vx *= 12 / speed; p.vy *= 12 / speed }
    p.x += p.vx; p.y += p.vy
  }
  separateNodes(positions, pinned)
  return Math.max(0, ...entries.map(([, p]) => Math.hypot(p.vx, p.vy)))
}
