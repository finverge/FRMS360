import type { GraphEdge, GraphNode } from "@/api/evidence"

export interface LaidOutNode extends GraphNode {
  x: number
  y: number
}

/** A small, dependency-free force-directed layout (repulsion + spring edges + weak
 * centering), run synchronously for a fixed iteration count. Good enough at the graph's
 * own cap (300 nodes - see routes/dashboards.py's graph() limit) without pulling in a
 * layout library for one view; a real physics engine is separately-scoped work if this
 * ever needs to animate or handle thousands of nodes. */
export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[], width: number, height: number): LaidOutNode[] {
  const sim = nodes.map((n, i) => {
    const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2
    const r = Math.min(width, height) * 0.35
    return { ...n, x: width / 2 + r * Math.cos(angle), y: height / 2 + r * Math.sin(angle), vx: 0, vy: 0 }
  })
  const indexOf = new Map(sim.map((n, i) => [n.id, i]))
  const edgePairs = edges
    .map((e) => [indexOf.get(e.source), indexOf.get(e.target)] as const)
    .filter((pair): pair is [number, number] => pair[0] !== undefined && pair[1] !== undefined)

  const REPULSION = 2200
  const SPRING = 0.02
  const IDEAL_LEN = 70
  const DAMPING = 0.85
  const CENTERING = 0.012
  const ITERATIONS = nodes.length > 150 ? 120 : 220

  for (let iter = 0; iter < ITERATIONS; iter++) {
    for (let i = 0; i < sim.length; i++) {
      for (let j = i + 1; j < sim.length; j++) {
        const dx = sim[i].x - sim[j].x
        const dy = sim[i].y - sim[j].y
        const distSq = Math.max(1, dx * dx + dy * dy)
        const dist = Math.sqrt(distSq)
        const force = REPULSION / distSq
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        sim[i].vx += fx; sim[i].vy += fy
        sim[j].vx -= fx; sim[j].vy -= fy
      }
    }
    for (const [a, b] of edgePairs) {
      const dx = sim[b].x - sim[a].x
      const dy = sim[b].y - sim[a].y
      const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy))
      const force = SPRING * (dist - IDEAL_LEN)
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      sim[a].vx += fx; sim[a].vy += fy
      sim[b].vx -= fx; sim[b].vy -= fy
    }
    for (const n of sim) {
      n.vx += (width / 2 - n.x) * CENTERING
      n.vy += (height / 2 - n.y) * CENTERING
      n.vx *= DAMPING
      n.vy *= DAMPING
      n.x += n.vx
      n.y += n.vy
      n.x = Math.max(16, Math.min(width - 16, n.x))
      n.y = Math.max(16, Math.min(height - 16, n.y))
    }
  }
  return sim
}
