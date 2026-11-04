/**
 * 思维导图的纯逻辑：索引、折叠、布局、搜索、过滤、本地合并。没有 React / 网络依赖，便于单元测试。
 * 工作区画布与阅读器右侧的紧凑树共用这里的函数。
 */
import type { BBox } from '@/lib/types'
import type { MindNode } from '@/lib/types.mindmap'

/** 超过这个节点数时，默认只展开到第 3 层（根为第 1 层），保证大图依然流畅、文字保持可读。 */
export const BIG_TREE = 60
export const DEFAULT_OPEN_DEPTH = 3

export interface TreeIndex {
  byId: Map<string, MindNode>
  children: Map<string, MindNode[]>
  root: MindNode | null
}

export function buildIndex(nodes: MindNode[]): TreeIndex {
  const byId = new Map<string, MindNode>()
  const children = new Map<string, MindNode[]>()
  let root: MindNode | null = null
  for (const n of nodes) {
    byId.set(n.id, n)
    if (n.parent_id === null) root = n
    else {
      const arr = children.get(n.parent_id)
      if (arr) arr.push(n)
      else children.set(n.parent_id, [n])
    }
  }
  for (const arr of children.values()) arr.sort((a, b) => a.ord - b.ord)
  return { byId, children, root }
}

/** 从根到该节点的祖先 id（不含自身），按由近到远。 */
export function ancestorsOf(index: TreeIndex, id: string): string[] {
  const out: string[] = []
  let cur = index.byId.get(id)
  const guard = new Set<string>()
  while (cur && cur.parent_id && !guard.has(cur.parent_id)) {
    out.push(cur.parent_id)
    guard.add(cur.parent_id)
    cur = index.byId.get(cur.parent_id)
  }
  return out
}

/** 每个节点是否有子节点以 child_count 为准（后端维护），避免依赖已加载的列表。 */
export function hasChildren(n: MindNode): boolean {
  return n.child_count > 0
}

/** 默认折叠：深度 ≥ openDepth 且有子节点的节点折叠，于是只显示到第 openDepth 层。 */
export function defaultCollapsed(nodes: MindNode[], openDepth = DEFAULT_OPEN_DEPTH): Set<string> {
  return new Set(nodes.filter((n) => n.depth >= openDepth && hasChildren(n)).map((n) => n.id))
}

/**
 * 初始折叠集合：用户保存过的折叠状态优先；没有任何保存的折叠状态且图很大时，默认折叠到第 2 层。
 */
export function initialCollapsed(nodes: MindNode[]): Set<string> {
  const saved = nodes.filter((n) => n.collapsed && hasChildren(n))
  if (saved.length > 0) return new Set(saved.map((n) => n.id))
  return nodes.length > BIG_TREE ? defaultCollapsed(nodes) : new Set()
}

/** 重新加载后保留本地折叠状态：仍然存在的节点沿用旧状态，新出现的节点按默认规则。 */
export function mergeCollapsed(prev: Set<string>, prevIds: Set<string>, nodes: MindNode[]): Set<string> {
  const next = new Set<string>()
  const big = nodes.length > BIG_TREE
  for (const n of nodes) {
    if (!hasChildren(n)) continue
    if (prevIds.has(n.id)) {
      if (prev.has(n.id)) next.add(n.id)
    } else if (big && n.depth >= DEFAULT_OPEN_DEPTH) next.add(n.id)
  }
  return next
}

/** 当前可见的节点（所有祖先都展开），保持前序。 */
export function visibleNodes(nodes: MindNode[], collapsed: Set<string>): MindNode[] {
  const index = buildIndex(nodes)
  const out: MindNode[] = []
  const walk = (n: MindNode): void => {
    out.push(n)
    if (collapsed.has(n.id)) return
    for (const c of index.children.get(n.id) ?? []) walk(c)
  }
  if (index.root) walk(index.root)
  return out
}

/** 展开到能看见某个节点：返回新的折叠集合（祖先全部展开）。 */
export function expandTo(collapsed: Set<string>, nodes: MindNode[], id: string): Set<string> {
  const index = buildIndex(nodes)
  const next = new Set(collapsed)
  for (const a of ancestorsOf(index, id)) next.delete(a)
  return next
}

export interface Box {
  x: number
  y: number
  w: number
  h: number
}

export const ROOT_H = 48

const CJK = /[⺀-鿿＀-￯]/

/** 估算一行文字的宽度：中日韩字符按一个字号宽，其余按 0.56 个字号。 */
export function textWidth(text: string, font: number): number {
  let w = 0
  for (const ch of text) w += CJK.test(ch) ? font : font * 0.56
  return w
}

/**
 * 节点尺寸：按标题长度估算（画布里的自定义节点用同样的结果，两边保持一致）。标题最多折两行。
 * 层级越深，框越“轻”：根是黑块，第二层是有框的方块，更深的是只带下划线的文字。
 */
export function nodeSize(n: Pick<MindNode, 'title' | 'depth'>): { w: number; h: number } {
  const root = n.depth === 1
  const d2 = n.depth === 2
  const font = root ? 16 : d2 ? 14 : 13.5
  const padX = root ? 26 : d2 ? 14 : 13
  const maxW = root ? 300 : 260
  const minW = root ? 160 : d2 ? 104 : 56
  // 第二层（逻辑主线的各个阶段）左边有一个序号圆章，要多留位置
  const full = textWidth(n.title, font) + padX * 2 + 4 + (d2 ? 28 : 0)
  const w = Math.round(Math.min(maxW, Math.max(minW, full)))
  const two = full > maxW
  const h = root ? ROOT_H : d2 ? (two ? 60 : 40) : two ? 50 : 32
  return { w, h }
}

/**
 * 从左到右的整齐树形布局：同级节点严格按顺序从上到下，父节点居中于它的子节点，同一层的节点不重叠。
 * （不用 dagre：它为了减少交叉会打乱同级顺序，画面上的上下顺序就和大纲对不上了。）
 * 用户拖动保存过位置（x、y 都不为 null）的节点沿用保存的位置。返回左上角坐标，xyflow 以左上角为节点原点。
 */
export function layoutTree(visible: MindNode[], opts: { rankSep?: number; nodeSep?: number } = {}): Map<string, Box> {
  const rankSep = opts.rankSep ?? 64
  const nodeSep = opts.nodeSep ?? 12
  const margin = 8
  const index = buildIndex(visible)
  const sizes = new Map(visible.map((n) => [n.id, nodeSize(n)]))

  // 每一层的 x：该层最宽的节点决定下一层的起点
  const maxW: number[] = []
  for (const n of visible) maxW[n.depth - 1] = Math.max(maxW[n.depth - 1] ?? 0, sizes.get(n.id)!.w)
  const xs: number[] = []
  let acc = margin
  for (let d = 0; d < maxW.length; d += 1) {
    xs[d] = acc
    acc += (maxW[d] ?? 0) + rankSep
  }

  const cy = new Map<string, number>() // 节点中心的 y
  const nextFree: number[] = [] // 每一层下一个节点最小的 top
  const shiftSubtree = (n: MindNode, delta: number): void => {
    for (const c of index.children.get(n.id) ?? []) {
      cy.set(c.id, (cy.get(c.id) as number) + delta)
      nextFree[c.depth - 1] = Math.max(nextFree[c.depth - 1] ?? 0, (cy.get(c.id) as number) + sizes.get(c.id)!.h / 2 + nodeSep)
      shiftSubtree(c, delta)
    }
  }
  const place = (n: MindNode): number => {
    const d = n.depth - 1
    const h = sizes.get(n.id)!.h
    const kids = index.children.get(n.id) ?? []
    let y: number
    if (kids.length === 0) {
      y = (nextFree[d] ?? margin) + h / 2
    } else {
      const cs = kids.map(place)
      y = (cs[0] + cs[cs.length - 1]) / 2
      const free = nextFree[d] ?? margin
      if (y - h / 2 < free) {
        const delta = free - (y - h / 2)
        shiftSubtree(n, delta)
        y += delta
      }
    }
    cy.set(n.id, y)
    nextFree[d] = y + h / 2 + nodeSep
    return y
  }
  if (index.root) place(index.root)

  const out = new Map<string, Box>()
  for (const n of visible) {
    const { w, h } = sizes.get(n.id)!
    const saved = n.x !== null && n.y !== null
    const y = cy.get(n.id) ?? margin + h / 2
    out.set(n.id, saved ? { x: n.x as number, y: n.y as number, w, h } : { x: xs[n.depth - 1] ?? margin, y: y - h / 2, w, h })
  }
  return out
}

/** 某个节点的全部后代 id（不含自身）。 */
export function descendantIds(index: TreeIndex, id: string): Set<string> {
  const out = new Set<string>()
  const walk = (nid: string): void => {
    for (const c of index.children.get(nid) ?? []) {
      out.add(c.id)
      walk(c.id)
    }
  }
  walk(id)
  return out
}

/** 图里最深的层级（根为 1）。 */
export function maxDepth(nodes: MindNode[]): number {
  return nodes.reduce((m, n) => Math.max(m, n.depth), 1)
}

/** “展开到第 N 层”：深度 ≥ N 且有子节点的节点折叠，于是只显示到第 N 层。N 大于最大深度时全部展开。 */
export function collapsedForDepth(nodes: MindNode[], depth: number): Set<string> {
  return new Set(nodes.filter((n) => n.depth >= depth && hasChildren(n)).map((n) => n.id))
}

/** 拖动节点后的移动方案：{ parentId, index } 与后端 PATCH 的 parent_id / index 对应（index 为去掉自身后的插入位置）。 */
export interface MovePlan {
  parentId: string
  index: number
}

/**
 * 把 dragId 拖到 overId 上：
 * - 与目标同一个父节点 → 同级调整顺序（拖在目标上半部分放到它前面，下半部分放到后面）；
 * - 否则 → 成为目标的最后一个子节点；
 * 根节点、拖到自己的后代上、拖到自己的父节点上（本来就是它的子节点）都不产生移动。
 */
export function planMove(nodes: MindNode[], dragId: string, overId: string, dragCenterY: number, overCenterY: number): MovePlan | null {
  const idx = buildIndex(nodes)
  const drag = idx.byId.get(dragId)
  const over = idx.byId.get(overId)
  if (!drag || !over || drag.id === over.id || drag.parent_id === null) return null
  if (descendantIds(idx, dragId).has(overId)) return null
  if (over.parent_id === drag.parent_id) {
    const sibs = (idx.children.get(drag.parent_id) ?? []).filter((s) => s.id !== dragId)
    const at = sibs.findIndex((s) => s.id === overId)
    if (at < 0) return null
    return { parentId: drag.parent_id, index: dragCenterY < overCenterY ? at : at + 1 }
  }
  if (over.id === drag.parent_id) return null
  return { parentId: over.id, index: (idx.children.get(over.id) ?? []).length }
}

export type NavDir = 'parent' | 'child' | 'prev' | 'next'

/** 键盘导航：← 父节点，→ 第一个子节点（需已展开），↑↓ 同级的上一个/下一个。没有可去的节点返回 null。 */
export function navigate(nodes: MindNode[], collapsed: Set<string>, id: string, dir: NavDir): string | null {
  const idx = buildIndex(nodes)
  const cur = idx.byId.get(id)
  if (!cur) return null
  if (dir === 'parent') return cur.parent_id
  if (dir === 'child') return collapsed.has(id) ? null : (idx.children.get(id)?.[0]?.id ?? null)
  if (!cur.parent_id) return null
  const sibs = idx.children.get(cur.parent_id) ?? []
  const i = sibs.findIndex((s) => s.id === id)
  const j = dir === 'prev' ? i - 1 : i + 1
  return sibs[j]?.id ?? null
}

/** 同级节点在父节点下的位置（0 起）；根节点或找不到时为 -1。 */
export function indexInParent(nodes: MindNode[], id: string): number {
  const idx = buildIndex(nodes)
  const n = idx.byId.get(id)
  if (!n || !n.parent_id) return -1
  return (idx.children.get(n.parent_id) ?? []).findIndex((s) => s.id === id)
}

/** 搜索：标题命中优先于摘要命中，保持图中的顺序。 */
export function searchNodes(nodes: MindNode[], q: string): string[] {
  const needle = q.trim().toLowerCase()
  if (!needle) return []
  const title: string[] = []
  const summary: string[] = []
  for (const n of nodes) {
    if (n.title.toLowerCase().includes(needle)) title.push(n.id)
    else if (n.summary.toLowerCase().includes(needle)) summary.push(n.id)
  }
  return [...title, ...summary]
}

/** 按类型过滤（空集合表示不过滤）。 */
export function filterByKind(nodes: MindNode[], kinds: Set<string>): MindNode[] {
  return kinds.size === 0 ? nodes : nodes.filter((n) => kinds.has(n.kind))
}

/** 图里出现过的类型及数量，按数量降序（用于左侧过滤条）。 */
export function kindCounts(nodes: MindNode[]): Array<{ kind: string; label: string; count: number }> {
  const m = new Map<string, { kind: string; label: string; count: number }>()
  for (const n of nodes) {
    if (n.kind === 'root') continue
    const e = m.get(n.kind)
    if (e) e.count += 1
    else m.set(n.kind, { kind: n.kind, label: n.kind_label, count: 1 })
  }
  return [...m.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'zh'))
}

/** 本地乐观更新：只改传入的字段。 */
export function patchLocal(nodes: MindNode[], id: string, patch: Partial<MindNode>): MindNode[] {
  return nodes.map((n) => (n.id === id ? { ...n, ...patch } : n))
}

/** 删除一个分支（节点及其所有后代），并更新父节点的子节点数。 */
export function removeBranch(nodes: MindNode[], id: string): MindNode[] {
  const index = buildIndex(nodes)
  const gone = new Set<string>()
  const walk = (nid: string): void => {
    gone.add(nid)
    for (const c of index.children.get(nid) ?? []) walk(c.id)
  }
  walk(id)
  const parent = index.byId.get(id)?.parent_id
  return nodes
    .filter((n) => !gone.has(n.id))
    .map((n) => (n.id === parent ? { ...n, child_count: Math.max(0, n.child_count - 1) } : n))
}

/** 前序遍历后的大纲行（用于阅读器右侧的紧凑树与左侧目录）。 */
export interface OutlineRow {
  node: MindNode
  level: number
  open: boolean
  expandable: boolean
}

export function outlineRows(nodes: MindNode[], collapsed: Set<string>): OutlineRow[] {
  return visibleNodes(nodes, collapsed).map((node) => ({
    node,
    level: node.depth - 1,
    expandable: hasChildren(node),
    open: !collapsed.has(node.id)
  }))
}

/** 节点的第一条可跳转依据（页码 + 区域）；分组节点没有直接依据时用汇总来的依据。 */
export function firstEvidence(n: MindNode): { page: number; bbox: BBox } | null {
  const r = n.refs[0]
  return r ? { page: r.page, bbox: r.bbox } : null
}

/** 把相对时间之外的“生成于”描述集中在这里，避免界面里各写一套。 */
export function viewStatusLabel(status: string, count: number): string {
  if (status === 'running') return '生成中'
  if (status === 'pending') return '排队中'
  if (status === 'failed') return count > 0 ? '上次重新生成失败' : '生成失败'
  return count > 0 ? `${count} 个节点` : '尚未生成'
}
