import dagre from '@dagrejs/dagre'
import type { GraphEdge, GraphNode, KbGraph } from '@/lib/api.export'

export interface GraphFilter {
  /** 要显示的节点类型；空集合 = 全部。 */
  types: Set<GraphNode['type']>
  /** 隐藏“归属主题 / 来自论文”这两种结构线，只看学术关系。 */
  hideStructure: boolean
  /** 只看有关系的条目（去掉孤立节点）。 */
  onlyConnected: boolean
}

export const DEFAULT_FILTER: GraphFilter = { types: new Set(), hideStructure: false, onlyConnected: false }

/** 按节点类型与边类型过滤；边的两端有一端被过滤掉时，这条边一起去掉。 */
export function filterGraph(g: KbGraph, f: GraphFilter): { nodes: GraphNode[]; edges: GraphEdge[] } {
  let nodes = g.nodes.filter((n) => f.types.size === 0 || f.types.has(n.type))
  const ids = new Set(nodes.map((n) => n.id))
  let edges = g.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && (!f.hideStructure || e.type === 'relation'))
  if (f.onlyConnected) {
    const linked = new Set(edges.flatMap((e) => [e.source, e.target]))
    nodes = nodes.filter((n) => linked.has(n.id))
    const keep = new Set(nodes.map((n) => n.id))
    edges = edges.filter((e) => keep.has(e.source) && keep.has(e.target))
  }
  return { nodes, edges }
}

export const NODE_W = 200
export const NODE_H = 56

export function nodeSize(n: GraphNode): { width: number; height: number } {
  // 标题越长越高（节点里最多显示 3 行）
  const lines = Math.min(3, Math.max(1, Math.ceil(n.label.length / 14)))
  return { width: NODE_W, height: 30 + lines * 17 }
}

export interface Placed {
  id: string
  x: number
  y: number
  width: number
  height: number
}

/** 从左到右分层排版：主题 → 条目 → 论文自然排成三列；关系线连接同列时 dagre 会自动错开。 */
export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): Map<string, Placed> {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 26, ranksep: 90, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of nodes) g.setNode(n.id, nodeSize(n))
  const known = new Set(nodes.map((n) => n.id))
  for (const e of edges) if (known.has(e.source) && known.has(e.target) && e.source !== e.target) g.setEdge(e.source, e.target)
  dagre.layout(g)
  const out = new Map<string, Placed>()
  for (const n of nodes) {
    const p = g.node(n.id)
    const { width, height } = nodeSize(n)
    out.set(n.id, { id: n.id, x: p.x - width / 2, y: p.y - height / 2, width, height })
  }
  return out
}

export const TYPE_LABEL: Record<GraphNode['type'], string> = { item: '知识条目', topic: '主题', paper: '论文' }
export const EDGE_LABEL: Record<GraphEdge['type'], string> = { relation: '学术关系', member: '归属主题', source: '来自论文' }

/** 一条边在图上的样式：已确认的学术关系 = 实线；AI 建议未确认 = 虚线；结构线 = 浅灰细线。 */
export function edgeStyle(e: GraphEdge): { dashed: boolean; strong: boolean; label: string } {
  if (e.type !== 'relation') return { dashed: false, strong: false, label: '' }
  const pending = e.status === 'suggested'
  return { dashed: pending, strong: !pending, label: pending ? `${e.label} · 待确认` : e.label }
}
