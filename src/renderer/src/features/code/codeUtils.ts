import dagre from '@dagrejs/dagre'
import type { Certainty, CodeFileEntry, CodeSymbol, ExplainTarget, FileRole, FlowEdge, FlowNode, PaperCodeLink } from '@/lib/types.code'

/* ---------------- 文件树 ---------------- */
export interface TreeNode {
  name: string
  path: string
  dir: boolean
  children: TreeNode[]
  file?: CodeFileEntry
}

const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' })

function sortNodes(nodes: TreeNode[]): void {
  nodes.sort((a, b) => (a.dir === b.dir ? collator.compare(a.name, b.name) : a.dir ? -1 : 1))
  for (const n of nodes) if (n.dir) sortNodes(n.children)
}

/** 构建文件树：目录在前、自然排序；只有一个子目录的目录链合并显示成 “a/b/c”。 */
export function buildFileTree(files: CodeFileEntry[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', dir: true, children: [] }
  const dirs = new Map<string, TreeNode>([['', root]])
  for (const f of files) {
    const parts = f.path.split('/')
    let parent = root
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts.slice(0, i + 1).join('/')
      let d = dirs.get(p)
      if (!d) {
        d = { name: parts[i], path: p, dir: true, children: [] }
        dirs.set(p, d)
        parent.children.push(d)
      }
      parent = d
    }
    parent.children.push({ name: parts[parts.length - 1], path: f.path, dir: false, children: [], file: f })
  }
  const compact = (n: TreeNode): TreeNode => {
    let cur = n
    while (cur.dir && cur.children.length === 1 && cur.children[0].dir) {
      const only = cur.children[0]
      cur = { ...only, name: `${cur.name}/${only.name}` }
    }
    return { ...cur, children: cur.children.map(compact) }
  }
  sortNodes(root.children)
  return root.children.map(compact)
}

/** 一个文件路径的所有上级目录路径（用来自动展开）。 */
export function ancestorDirs(path: string): string[] {
  const parts = path.split('/')
  const out: string[] = []
  for (let i = 1; i < parts.length; i++) out.push(parts.slice(0, i).join('/'))
  return out
}

export function filterFiles(files: CodeFileEntry[], q: string, limit = 200): CodeFileEntry[] {
  const needle = q.trim().toLowerCase()
  if (!needle) return []
  const terms = needle.split(/\s+/)
  const out: CodeFileEntry[] = []
  for (const f of files) {
    const p = f.path.toLowerCase()
    if (terms.every((t) => p.includes(t))) {
      out.push(f)
      if (out.length >= limit) break
    }
  }
  return out
}

/* ---------------- 标签与格式 ---------------- */
export const ROLE_LABEL: Record<FileRole, string> = {
  entry: '入口', config: '配置', dependency: '依赖', data: '数据', model: '模型', train: '训练', test: '测试/评估', util: '工具', doc: '文档', other: '其它'
}

/** 在文件树里值得标注的角色（工具/其它不标，避免满屏噪声）。 */
export function roleBadge(role: FileRole): string | null {
  return ['entry', 'train', 'test', 'model', 'data', 'config'].includes(role) ? ROLE_LABEL[role] : null
}

export const CERTAINTY_LABEL: Record<Certainty, string> = {
  resolved: '确定',
  name: '按名称匹配',
  ambiguous: '多个候选',
  external: '外部库'
}

export function isUncertain(c: Certainty): boolean {
  return c === 'name' || c === 'ambiguous'
}

export const ORIGIN_LABEL: Record<string, string> = {
  argparse: '命令行参数默认值',
  constant: '常量/赋值',
  kwarg: '调用时的关键字参数',
  config: '配置文件',
  yaml: '配置文件',
  json: '配置文件'
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} KB`
  return `${(n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : '—'
}

export function lineRange(a: number, b: number): string {
  return a === b ? `L${a}` : `L${a}–${b}`
}

export function pathBase(path: string): string {
  return path.split('/').pop() ?? path
}

/** "path:12-30" 或 "path:12" → 结构化；用于 also_in 之类的字符串。 */
export function parseLoc(loc: string): { path: string; a: number; b: number } | null {
  const m = /^(.*):(\d+)(?:-(\d+))?$/.exec(loc)
  if (!m) return null
  const a = Number(m[2])
  return { path: m[1], a, b: m[3] ? Number(m[3]) : a }
}

/* ---------------- 解释对象 ---------------- */
export function targetLabel(t: ExplainTarget): string {
  const where = t.path ? pathBase(t.path) : ''
  switch (t.kind) {
    case 'file':
      return `文件 ${where || t.path || ''}`.trim()
    case 'repo':
      return '整个仓库的总讲解'
    case 'module':
      return `目录 ${t.path || '仓库根目录'}`
    case 'function':
      return `函数 ${t.symbol ?? where ?? ''}`.trim()
    case 'class':
      return `类 ${t.symbol ?? where ?? ''}`.trim()
    case 'algorithm':
      return `算法步骤 ${t.symbol ?? where}`.trim()
    case 'dataflow':
      return `数据流 ${t.symbol ?? where}`.trim()
    case 'variable':
      return `变量 ${t.variable ?? ''}${t.symbol ? ` · 在 ${t.symbol} 中` : where ? ` · ${where}` : ''}`.trim()
    case 'calls':
      return `调用关系 ${t.symbol ?? ''}`.trim()
    case 'line_range':
      return `${where} ${t.line_start && t.line_end ? lineRange(t.line_start, t.line_end) : ''}`.trim()
  }
}

/** 当前默认的解释对象：选中的行 > 选中的符号 > 整个文件。 */
export function deriveTarget(
  openPath: string | null,
  lineSel: { path: string; a: number; b: number } | null,
  symbol: Pick<CodeSymbol, 'id' | 'path' | 'kind' | 'qualname'> | null
): ExplainTarget | null {
  if (!openPath) return null
  if (lineSel && lineSel.path === openPath) return { kind: 'line_range', path: openPath, line_start: lineSel.a, line_end: lineSel.b }
  if (symbol && symbol.path === openPath && symbol.kind !== 'module') {
    return { kind: symbol.kind === 'class' ? 'class' : 'function', path: openPath, symbol_id: symbol.id, symbol: symbol.qualname }
  }
  return { kind: 'file', path: openPath }
}

/* ---------------- 引用标记 [L3] ---------------- */
export type CiteSegment = { type: 'text'; text: string } | { type: 'cite'; refs: number[]; raw: string }

/** 把回答里的 [L1] / [L1][L2] / [L1,L2] 拆出来，渲染成可点击的证据标记。 */
export function splitCitations(text: string): CiteSegment[] {
  const out: CiteSegment[] = []
  const re = /\[L(\d+(?:\s*[,，]\s*L?\d+)*)\]/g
  let last = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push({ type: 'text', text: text.slice(last, m.index) })
    const refs = m[1].split(/[,，]/).map((s) => Number(s.replace(/\D/g, '')))
    out.push({ type: 'cite', refs: refs.filter((n) => n > 0), raw: m[0] })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ type: 'text', text: text.slice(last) })
  return out
}

/** 给 react-markdown 用：[L2] → [L2](lref:2)，由自定义链接渲染成按钮。 */
export function citationsToLinks(text: string): string {
  return text.replace(/\[L(\d+(?:\s*[,，]\s*L?\d+)*)\]/g, (_m, g: string) => {
    const refs = g
      .split(/[,，]/)
      .map((s) => s.replace(/\D/g, ''))
      .filter(Boolean)
    return `[${refs.map((r) => `L${r}`).join(',')}](lref:${refs.join(',')})`
  })
}

/** 流式输出期间，模型原始的 [C1:3-5] / [path:3-5] 标记还没被核实改写，先弱化显示。 */
export function dimRawCitations(text: string): string {
  return text.replace(/\[(?:C\d+(?::\d+(?:-\d+)?)?|[\w./\\-]+\.\w+:\d+(?:-\d+)?)\]/g, (m) => `\`${m}\``)
}

/* ---------------- 流程图布局 ---------------- */
export const FLOW_NODE_W = 224
export const FLOW_NODE_H = 60

export interface FlowLayout {
  positions: Map<number, { x: number; y: number }>
  width: number
  height: number
}

/** 用 dagre 排版调用流程：从左到右 = 调用的深度，同一列从上到下 = 执行顺序（一个函数里先调用的在上面）。 */
export function layoutFlow(nodes: FlowNode[], edges: FlowEdge[]): FlowLayout {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 16, ranksep: 60, marginx: 24, marginy: 24, ranker: 'network-simplex' })
  g.setDefaultEdgeLabel(() => ({}))
  const ordered = [...nodes].sort((a, b) => a.order - b.order)
  for (const n of ordered) g.setNode(String(n.id), { width: FLOW_NODE_W, height: FLOW_NODE_H })
  const seen = new Set<string>()
  for (const e of [...edges].sort((a, b) => a.order - b.order)) {
    const k = `${e.source}>${e.target}`
    if (seen.has(k) || e.source === e.target || !g.hasNode(String(e.source)) || !g.hasNode(String(e.target))) continue
    seen.add(k)
    g.setEdge(String(e.source), String(e.target))
  }
  dagre.layout(g)
  // dagre 只保证同一列内不重叠，不保证顺序；这里把每列已有的纵向位置按调用顺序（order）重新分配，
  // 让“先调用的在上”成立，同时不改变边的交叉优化已给出的列与间距。
  const columns = new Map<number, FlowNode[]>()
  for (const n of ordered) {
    const p = g.node(String(n.id))
    if (!p) continue
    const col = columns.get(p.x)
    if (col) col.push(n)
    else columns.set(p.x, [n])
  }
  for (const col of columns.values()) {
    const slots = col.map((n) => g.node(String(n.id)).y).sort((a, b) => a - b)
    col.forEach((n, i) => {
      g.node(String(n.id)).y = slots[i]
    })
  }
  const positions = new Map<number, { x: number; y: number }>()
  let width = 0
  let height = 0
  for (const n of ordered) {
    const p = g.node(String(n.id))
    if (!p) continue
    positions.set(n.id, { x: p.x - FLOW_NODE_W / 2, y: p.y - FLOW_NODE_H / 2 })
    width = Math.max(width, p.x + FLOW_NODE_W / 2)
    height = Math.max(height, p.y + FLOW_NODE_H / 2)
  }
  return { positions, width, height }
}

/* ---------------- 论文 ↔ 代码 ---------------- */
export interface ModuleGroup {
  key: string
  name: string
  description: string
  pages: number[]
  section: string | null
  links: PaperCodeLink[]
}

/** 按论文模块名把关联分组；组内“很可能”在前，其次已确认优先。已拒绝的默认隐藏。 */
export function groupLinksByModule(links: PaperCodeLink[], showRejected = false): ModuleGroup[] {
  const map = new Map<string, ModuleGroup>()
  for (const l of links) {
    if (l.status === 'rejected' && !showRejected) continue
    const key = l.module.name
    let g = map.get(key)
    if (!g) {
      g = { key, name: l.module.name, description: l.module.description ?? '', pages: l.module.pages ?? [], section: l.module.section ?? null, links: [] }
      map.set(key, g)
    }
    g.links.push(l)
  }
  const rank = (l: PaperCodeLink): number => (l.status === 'confirmed' ? 0 : 1) * 10 + (l.confidence === 'likely' ? 0 : 1)
  for (const g of map.values()) g.links.sort((a, b) => rank(a) - rank(b))
  return [...map.values()]
}

export const CONFIDENCE_LABEL: Record<PaperCodeLink['confidence'], string> = { likely: '很可能对应', possible: '可能相关' }
export const METHOD_LABEL: Record<PaperCodeLink['method'], string> = { llm: 'AI 判断', lexical: '词法匹配', manual: '手动关联' }
export const LINK_STATUS_LABEL: Record<PaperCodeLink['status'], string> = { suggested: '待确认', confirmed: '已确认', rejected: '已拒绝' }
