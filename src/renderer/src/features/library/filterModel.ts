import type { PaperListQuery } from '@/lib/api'
import type { FolderItem, FolderNode, PaperFilters, ParseState, ReadState } from '@/lib/types.library'

export const MAX_FOLDER_DEPTH = 6

const READ_LABEL: Record<ReadState, string> = { unread: '未读', reading: '在读', done: '读完' }
const PARSE_LABEL: Record<ParseState, string> = {
  ready: '解析完成',
  parsing: '解析中',
  failed: '解析失败',
  metadata_only: '仅元数据',
  ocr: '扫描版 OCR'
}
export const READ_OPTIONS = (Object.keys(READ_LABEL) as ReadState[]).map((v) => ({ value: v, label: READ_LABEL[v] }))
export const PARSE_OPTIONS = (Object.keys(PARSE_LABEL) as ParseState[]).map((v) => ({ value: v, label: PARSE_LABEL[v] }))

type TextKey = 'note_q' | 'annotation_q' | 'text_q' | 'card_q'
export const TEXT_FILTERS: Array<{ key: TextKey; label: string; placeholder: string; hint?: string }> = [
  { key: 'note_q', label: '笔记内容', placeholder: '搜索你写过的笔记' },
  { key: 'annotation_q', label: '批注内容', placeholder: '搜索批注和高亮的文字' },
  { key: 'text_q', label: '论文全文', placeholder: '搜索论文正文' },
  { key: 'card_q', label: 'AI 卡片', placeholder: '搜索 AI 研究卡片', hint: '这是 AI 生成的内容，不是论文原文' }
]
export const FLAG_FILTERS = [
  { key: 'has_notes', label: '有笔记' },
  { key: 'has_annotations', label: '有批注或高亮' },
  { key: 'has_supplement', label: '有补充材料' },
  { key: 'has_code', label: '已获取代码' },
  { key: 'favorite', label: '已收藏' }
] as const

const clean = (s: string | undefined): string => (s ?? '').trim()

/** 去掉空值，得到“真正生效”的条件。 */
export function normalizeFilters(f: PaperFilters): PaperFilters {
  const out: PaperFilters = {}
  for (const k of ['note_q', 'annotation_q', 'text_q', 'card_q', 'author', 'venue', 'language'] as const) {
    const v = clean(f[k])
    if (v) out[k] = v
  }
  if (f.tags && f.tags.length > 0) {
    out.tags = [...new Set(f.tags)]
    if (f.tag_mode === 'all') out.tag_mode = 'all'
  }
  if (f.folder) out.folder = f.folder
  if (typeof f.year_from === 'number' && Number.isFinite(f.year_from)) out.year_from = f.year_from
  if (typeof f.year_to === 'number' && Number.isFinite(f.year_to)) out.year_to = f.year_to
  if (f.read) out.read = f.read
  if (f.parse) out.parse = f.parse
  for (const k of ['has_notes', 'has_annotations', 'has_supplement', 'has_code', 'favorite'] as const) {
    if (f[k] === true) out[k] = true // 只提供“必须有”；不提供“必须没有”，避免界面上多出难懂的三态
  }
  if (f.imported_from) out.imported_from = f.imported_from
  if (f.imported_to) out.imported_to = f.imported_to
  return out
}

export function isEmptyFilters(f: PaperFilters): boolean {
  return Object.keys(normalizeFilters(f)).length === 0
}

/** yyyy-mm-dd 的次日（后端的 imported_to 是“早于”，所以要把“含当天”换成次日零点）。 */
export function nextDay(date: string): string {
  const d = new Date(`${date}T00:00:00Z`)
  if (Number.isNaN(d.getTime())) return date
  d.setUTCDate(d.getUTCDate() + 1)
  return d.toISOString().slice(0, 10)
}

/** 筛选条件 → GET /papers 的查询参数。 */
export function filtersToQuery(f: PaperFilters): Partial<PaperListQuery> {
  const n = normalizeFilters(f)
  const q: Partial<PaperListQuery> = {}
  if (n.note_q) q.note_q = n.note_q
  if (n.annotation_q) q.annotation_q = n.annotation_q
  if (n.text_q) q.text_q = n.text_q
  if (n.card_q) q.card_q = n.card_q
  if (n.tags) {
    q.tags = n.tags.join(',')
    q.tag_mode = n.tag_mode ?? 'any'
  }
  if (n.folder) q.folder = n.folder
  if (n.year_from !== undefined) q.year_from = n.year_from
  if (n.year_to !== undefined) q.year_to = n.year_to
  if (n.author) q.author = n.author
  if (n.venue) q.venue = n.venue
  if (n.language) q.language = n.language
  if (n.read) q.read = n.read
  if (n.parse) q.parse = n.parse
  for (const k of ['has_notes', 'has_annotations', 'has_supplement', 'has_code', 'favorite'] as const) if (n[k]) q[k] = true
  if (n.imported_from) q.imported_from = n.imported_from
  if (n.imported_to) q.imported_to = nextDay(n.imported_to)
  return q
}

/** 有文字类条件（会出现命中片段）时，默认按相关度排序。 */
export function hasTextQuery(f: PaperFilters, q: string): boolean {
  return Boolean(clean(q) || clean(f.note_q) || clean(f.annotation_q) || clean(f.text_q) || clean(f.card_q))
}

// ---------------------------------------------------------------- URL 序列化
export function serializeFilters(f: PaperFilters): string {
  const n = normalizeFilters(f)
  return Object.keys(n).length ? JSON.stringify(n) : ''
}

/** 从 URL 里读回筛选条件；坏数据一律当没有（不抛错）。 */
export function parseFilters(raw: string | null | undefined): PaperFilters {
  if (!raw) return {}
  try {
    const v = JSON.parse(raw) as unknown
    if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
    const o = v as Record<string, unknown>
    const f: PaperFilters = {}
    for (const k of ['note_q', 'annotation_q', 'text_q', 'card_q', 'author', 'venue', 'language', 'folder', 'imported_from', 'imported_to'] as const) {
      if (typeof o[k] === 'string') f[k] = o[k] as string
    }
    if (Array.isArray(o.tags)) f.tags = o.tags.filter((t): t is string => typeof t === 'string')
    if (o.tag_mode === 'all') f.tag_mode = 'all'
    for (const k of ['year_from', 'year_to'] as const) if (typeof o[k] === 'number') f[k] = o[k] as number
    if (o.read === 'unread' || o.read === 'reading' || o.read === 'done') f.read = o.read
    if (typeof o.parse === 'string' && PARSE_OPTIONS.some((p) => p.value === o.parse)) f.parse = o.parse as ParseState
    for (const k of ['has_notes', 'has_annotations', 'has_supplement', 'has_code', 'favorite'] as const) if (o[k] === true) f[k] = true
    return normalizeFilters(f)
  } catch {
    return {}
  }
}

// ---------------------------------------------------------------- 筛选芯片
export interface FilterChip {
  key: string
  label: string
  /** 移除这一枚芯片后的条件 */
  without: PaperFilters
}

export interface ChipLookup {
  tagName: (id: string) => string
  folderPath: (id: string) => string
}

export function filterChips(f: PaperFilters, lookup: ChipLookup): FilterChip[] {
  const n = normalizeFilters(f)
  const chips: FilterChip[] = []
  const drop = (...keys: Array<keyof PaperFilters>): PaperFilters => {
    const c: PaperFilters = { ...n }
    for (const k of keys) delete c[k]
    return c
  }
  const short = (s: string): string => (s.length > 18 ? `${s.slice(0, 18)}…` : s)
  if (n.folder) chips.push({ key: 'folder', label: n.folder === 'none' ? '文件夹：未分类' : `文件夹：${lookup.folderPath(n.folder)}`, without: drop('folder') })
  if (n.note_q) chips.push({ key: 'note_q', label: `笔记：${short(n.note_q)}`, without: drop('note_q') })
  if (n.annotation_q) chips.push({ key: 'annotation_q', label: `批注：${short(n.annotation_q)}`, without: drop('annotation_q') })
  if (n.text_q) chips.push({ key: 'text_q', label: `全文：${short(n.text_q)}`, without: drop('text_q') })
  if (n.card_q) chips.push({ key: 'card_q', label: `AI 卡片：${short(n.card_q)}`, without: drop('card_q') })
  if (n.tags) {
    const names = n.tags.map(lookup.tagName).join(n.tag_mode === 'all' ? ' + ' : ' / ')
    chips.push({ key: 'tags', label: `标签：${names}${n.tags.length > 1 ? (n.tag_mode === 'all' ? ' · 全部' : ' · 任一') : ''}`, without: drop('tags', 'tag_mode') })
  }
  if (n.year_from !== undefined || n.year_to !== undefined) {
    const label = n.year_from !== undefined && n.year_to !== undefined ? (n.year_from === n.year_to ? `${n.year_from} 年` : `${n.year_from}–${n.year_to} 年`) : n.year_from !== undefined ? `${n.year_from} 年起` : `${n.year_to} 年前`
    chips.push({ key: 'year', label: `年份：${label}`, without: drop('year_from', 'year_to') })
  }
  if (n.author) chips.push({ key: 'author', label: `作者：${short(n.author)}`, without: drop('author') })
  if (n.venue) chips.push({ key: 'venue', label: `来源：${short(n.venue)}`, without: drop('venue') })
  if (n.language) chips.push({ key: 'language', label: `语言：${n.language === 'zh' ? '中文' : n.language === 'en' ? '英文' : n.language}`, without: drop('language') })
  if (n.read) chips.push({ key: 'read', label: `阅读：${READ_LABEL[n.read]}`, without: drop('read') })
  if (n.parse) chips.push({ key: 'parse', label: `解析：${PARSE_LABEL[n.parse]}`, without: drop('parse') })
  for (const flag of FLAG_FILTERS) if (n[flag.key]) chips.push({ key: flag.key, label: flag.label, without: drop(flag.key) })
  if (n.imported_from || n.imported_to) {
    chips.push({ key: 'imported', label: `导入：${n.imported_from ?? '…'} 至 ${n.imported_to ?? '…'}`, without: drop('imported_from', 'imported_to') })
  }
  return chips
}

// ---------------------------------------------------------------- 文件夹树
export function buildFolderTree(items: FolderItem[]): FolderNode[] {
  const byParent = new Map<string | null, FolderItem[]>()
  for (const f of items) {
    const k = f.parent_id && items.some((x) => x.id === f.parent_id) ? f.parent_id : null
    byParent.set(k, [...(byParent.get(k) ?? []), f])
  }
  const make = (parent: string | null, depth: number): FolderNode[] =>
    (byParent.get(parent) ?? [])
      .slice()
      .sort((a, b) => a.sort - b.sort || a.name.localeCompare(b.name, 'zh'))
      .map((f) => ({ ...f, depth, children: make(f.id, depth + 1) }))
  return make(null, 0)
}

export function flattenTree(nodes: FolderNode[], expanded: ReadonlySet<string>): FolderNode[] {
  const out: FolderNode[] = []
  const walk = (list: FolderNode[]): void => {
    for (const n of list) {
      out.push(n)
      if (expanded.has(n.id)) walk(n.children)
    }
  }
  walk(nodes)
  return out
}

export function descendantIds(items: FolderItem[], id: string): string[] {
  const out: string[] = []
  const stack = [id]
  while (stack.length) {
    const cur = stack.pop() as string
    out.push(cur)
    for (const f of items) if (f.parent_id === cur) stack.push(f.id)
  }
  return out
}

export function folderPath(items: FolderItem[], id: string): string {
  const names: string[] = []
  let cur: string | null = id
  for (let guard = 0; cur && guard < 20; guard++) {
    const f = items.find((x) => x.id === cur)
    if (!f) break
    names.push(f.name)
    cur = f.parent_id
  }
  return names.reverse().join(' / ')
}

function depthOf(items: FolderItem[], id: string | null): number {
  let d = 0
  let cur = id
  while (cur && d < 50) {
    cur = items.find((x) => x.id === cur)?.parent_id ?? null
    d++
  }
  return d
}

function heightOf(items: FolderItem[], id: string): number {
  const kids = items.filter((f) => f.parent_id === id)
  return 1 + (kids.length ? Math.max(...kids.map((k) => heightOf(items, k.id))) : 0)
}

/** 把文件夹 dragId 放进 targetId（null = 最上层）是否合法：不能放进自己或自己的子孙里，也不能超过最大层数。 */
export function canNestFolder(items: FolderItem[], dragId: string, targetId: string | null): boolean {
  if (targetId === dragId) return false
  if (targetId && descendantIds(items, dragId).includes(targetId)) return false
  const cur = items.find((f) => f.id === dragId)
  if (!cur) return false
  if (cur.parent_id === targetId) return true
  return depthOf(items, targetId) + heightOf(items, dragId) <= MAX_FOLDER_DEPTH
}

/** 在同级里把 dragId 挪到 beforeId 之前（beforeId=null 放到最后），返回新的 id 顺序。 */
export function reorderIds(siblings: string[], dragId: string, beforeId: string | null): string[] {
  const rest = siblings.filter((id) => id !== dragId)
  const at = beforeId === null ? rest.length : rest.indexOf(beforeId)
  rest.splice(at < 0 ? rest.length : at, 0, dragId)
  return rest
}

// ---------------------------------------------------------------- 拖放载荷
export const DRAG_PAPERS = 'application/x-paperdesk-papers'
export const DRAG_FOLDER = 'application/x-paperdesk-folder'

/** 拖拽一篇论文时：它在当前选择里就一起拖整个选择，否则只拖它自己。 */
export function dragPaperIds(paperId: string, selected: ReadonlySet<string>): string[] {
  return selected.has(paperId) ? [...selected] : [paperId]
}

// ---------------------------------------------------------------- 命中片段
/** 把片段按 hit 下标切成 [前, 命中, 后]，界面里命中部分用黑底白字高亮。 */
export function splitSnippet(text: string, hit: [number, number]): [string, string, string] {
  const a = Math.max(0, Math.min(text.length, hit[0]))
  const b = Math.max(a, Math.min(text.length, hit[1]))
  return [text.slice(0, a), text.slice(a, b), text.slice(b)]
}
