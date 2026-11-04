import dagre from '@dagrejs/dagre'
import type { FrameworkData, FrameworkFlow } from '@/lib/types.vision'

/** 把视觉模型读出的框架图（模块、连线、分组）排成流程图的位置。纯函数，不依赖界面。 */
export interface LaidModule {
  id: string
  label: string
  /** 左上角坐标；有分组时是相对分组左上角的位置（xyflow 子节点的约定） */
  x: number
  y: number
  w: number
  h: number
  group: string | null
}
export interface LaidGroup {
  id: string
  label: string
  x: number
  y: number
  w: number
  h: number
}
export interface LaidEdge {
  id: string
  from: string
  to: string
  label: string
  kind: FrameworkFlow['kind']
  /** 任一端的模块名没有在图的文字层里找到：连线画成虚线，不当作确定的结构 */
  uncertain: boolean
}
export interface FrameworkLayout {
  modules: LaidModule[]
  groups: LaidGroup[]
  edges: LaidEdge[]
  width: number
  height: number
  /** 被丢弃的连线数：指向不存在的模块、自环或重复 */
  droppedEdges: number
}

const GROUP_PAD = 22
const GROUP_HEAD = 26

/** 估算一行文字的像素宽：中日韩字符约 13px，其它约 7px。 */
export function textWidth(s: string): number {
  let w = 0
  for (const ch of s) w += /[　-鿿＀-￯]/.test(ch) ? 13 : 7
  return w
}

export function moduleSize(label: string): { w: number; h: number } {
  const w = Math.min(220, Math.max(92, Math.ceil(textWidth(label)) + 30))
  return { w, h: textWidth(label) > 190 ? 52 : 38 }
}

export function layoutFramework(data: FrameworkData): FrameworkLayout {
  const g = new dagre.graphlib.Graph({ compound: true })
  g.setGraph({ rankdir: 'LR', nodesep: 22, ranksep: 64, marginx: 16, marginy: 16 })
  g.setDefaultEdgeLabel(() => ({}))

  const moduleIds = new Set(data.modules.map((m) => m.id))
  // 分组：成员以 groups[].members 为准，其次用模块自己的 group 字段；一个模块只属于一个分组
  const groupOf = new Map<string, string>()
  const groupIds = new Set(data.groups.map((x) => x.id))
  for (const grp of data.groups) for (const m of grp.members) if (moduleIds.has(m) && !groupOf.has(m)) groupOf.set(m, grp.id)
  for (const m of data.modules) if (!groupOf.has(m.id) && m.group && groupIds.has(m.group)) groupOf.set(m.id, m.group)
  const usedGroups = data.groups.filter((x) => [...groupOf.values()].includes(x.id))

  for (const grp of usedGroups) g.setNode(grp.id, { label: grp.label })
  const sizes = new Map<string, { w: number; h: number }>()
  for (const m of data.modules) {
    const sz = moduleSize(m.label)
    sizes.set(m.id, sz)
    g.setNode(m.id, { width: sz.w, height: sz.h })
    const gid = groupOf.get(m.id)
    if (gid) g.setParent(m.id, gid)
  }

  const inText = new Map(data.modules.map((m) => [m.id, m.in_text]))
  const edges: LaidEdge[] = []
  const seen = new Set<string>()
  let dropped = 0
  data.flows.forEach((f, i) => {
    const key = `${f.from}>${f.to}`
    if (!moduleIds.has(f.from) || !moduleIds.has(f.to) || f.from === f.to || seen.has(key)) {
      dropped += 1
      return
    }
    seen.add(key)
    g.setEdge(f.from, f.to)
    edges.push({
      id: `e${i}`, from: f.from, to: f.to, label: f.label ?? '', kind: f.kind,
      uncertain: inText.get(f.from) === false || inText.get(f.to) === false
    })
  })

  dagre.layout(g)

  const groups: LaidGroup[] = []
  const origin = new Map<string, { x: number; y: number }>()
  for (const grp of usedGroups) {
    const p = g.node(grp.id)
    if (!p || !Number.isFinite(p.x)) continue
    // dagre 的分组框只包住成员；这里再留出内边距和标题行
    const x = p.x - p.width / 2 - GROUP_PAD
    const y = p.y - p.height / 2 - GROUP_PAD - GROUP_HEAD
    groups.push({ id: grp.id, label: grp.label, x, y, w: p.width + GROUP_PAD * 2, h: p.height + GROUP_PAD * 2 + GROUP_HEAD })
    origin.set(grp.id, { x, y })
  }

  let width = 0
  let height = 0
  const modules: LaidModule[] = data.modules.map((m) => {
    const p = g.node(m.id)
    const sz = sizes.get(m.id)!
    const absX = p.x - sz.w / 2
    const absY = p.y - sz.h / 2
    const gid = groupOf.get(m.id) ?? null
    const o = gid ? origin.get(gid) : undefined
    width = Math.max(width, absX + sz.w)
    height = Math.max(height, absY + sz.h)
    return { id: m.id, label: m.label, x: o ? absX - o.x : absX, y: o ? absY - o.y : absY, w: sz.w, h: sz.h, group: o ? gid : null }
  })
  for (const grp of groups) {
    width = Math.max(width, grp.x + grp.w)
    height = Math.max(height, grp.y + grp.h)
  }
  return { modules, groups, edges, width, height, droppedEdges: dropped }
}
