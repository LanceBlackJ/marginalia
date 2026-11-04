import { Graph, layout } from '@dagrejs/dagre'
import type { CompareEdge, CompareResult } from '@/lib/types.discovery'

export const NODE_W = 230
export const NODE_H = 64

export const BASIS_LABEL: Record<CompareEdge['basis'], string> = {
  openalex: 'OpenAlex 参考文献数据',
  text_reference: '论文参考文献列表匹配',
  ai: 'AI 推断'
}

export interface EdgeGroup {
  id: string
  from: string
  to: string
  edges: CompareEdge[]
}

/** 同一对论文（同一方向）之间的多条关系合并为一条连线。 */
export function groupEdges(edges: CompareEdge[]): EdgeGroup[] {
  const map = new Map<string, EdgeGroup>()
  for (const e of edges) {
    const id = `${e.from}>${e.to}`
    const g = map.get(id) ?? { id, from: e.from, to: e.to, edges: [] }
    g.edges.push(e)
    map.set(id, g)
  }
  return [...map.values()]
}

/** 用 dagre 为论文节点排布位置（从左到右）；返回每个节点左上角坐标。 */
export function layoutGraph(nodes: CompareResult['graph']['nodes'], groups: EdgeGroup[]): Map<string, { x: number; y: number }> {
  const g = new Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 36, ranksep: 120, marginx: 12, marginy: 12 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H })
  for (const e of groups) {
    if (nodes.some((n) => n.id === e.from) && nodes.some((n) => n.id === e.to)) g.setEdge(e.from, e.to)
  }
  layout(g)
  const out = new Map<string, { x: number; y: number }>()
  for (const n of nodes) {
    const p = g.node(n.id)
    out.set(n.id, { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 })
  }
  return out
}
