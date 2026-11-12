/** 文字阅读器的纯函数：把解析出的段落整理成“连续的章节与段落”结构，并提供定位、高亮与导出。 */
import type { TextItem } from '@/lib/api.text'
import type { BBox, OutlineItem } from '@/lib/types'
import { splitFront, type FrontInfo } from './frontMatter'
import { joinParts } from './textClean'

export type ReadView = 'text' | 'both' | 'target' | 'pdf' | 'split'
export const READ_VIEWS: ReadView[] = ['text', 'both', 'target', 'pdf', 'split']
/** 文字界面本身的三种呈现（并排视图里文字栏也用其中一种） */
export type TextView = Exclude<ReadView, 'pdf' | 'split'>

export type Block =
  | { type: 'heading'; key: string; title: string; level: number; page: number; /** 对应目录项的章节键（见 secKey），同名小节靠它区分 */ secKey?: string }
  /** parts：被页边界切开的同一个段落的各段（至少一段）；显示时接成一段，复制/定位仍按各段序号 */
  | { type: 'passage'; item: TextItem; parts: TextItem[]; pageStart: boolean }

export interface TextDoc {
  /** 开头的前置信息（DOI、作者、单位、日期…），已从正文里拿出来；没有“首部信息”章节时为 null */
  front: FrontInfo | null
  /** 摘要段落，单独成块显示 */
  abstract: TextItem[]
  /** 连续的正文：不按页分块，页码只作为每个块的属性 */
  blocks: Block[]
  /** 参考文献单独成节，默认折叠 */
  references: TextItem[]
  /** 因“图内文字”默认隐藏而没显示的段落数 */
  hidden: number
}

/** 解析器给“第一个标题之前的正文”起的占位章节名：不当作标题显示。 */
export const IMPLICIT_BODY = '正文'

/** 章节键：目录项和正文标题共用，用章节 id（带 # 前缀，不会和标题文字撞）。同名小节（两个 “Results”）只有 id 能区分。 */
export function secKey(id: string | null | undefined): string | undefined {
  return id === null || id === undefined || id === '' ? undefined : `#${id}`
}

/** 章节标题 → 标题级别（1 为最高层）。同名标题取第一次出现。 */
export function headingLevels(outline: OutlineItem[]): Map<string, number> {
  const out = new Map<string, number>()
  if (outline.length === 0) return out
  const min = Math.min(...outline.map((o) => o.level))
  for (const o of outline) if (!out.has(o.title)) out.set(o.title, Math.min(4, Math.max(1, o.level - min + 1)))
  return out
}

const SENTENCE_END = /[.?!:;”"’)\]]\s*$/

/** 下一段确实换了页，或换了栏（同一页里跑到右边那一栏的上方）。 */
function crossesPageOrColumn(prev: TextItem, next: TextItem): boolean {
  if (next.page > prev.page) return true
  return next.bbox[1] < prev.bbox[1] - 50 && next.bbox[0] > prev.bbox[0] + 80
}

/**
 * 上一段在页尾或栏尾被切断：末尾没有句末标点，下一段（下一页，或同一页换栏后）接着写。
 * - 下一段以小写字母开头：一定是接着的。
 * - 以大写开头（“…from the Hugging | Face community…”，专有名词被栏尾切开）：只有确实换了页/栏、上一段够长时才接。
 * 同一页内只在上一段足够长（不是一行小标题）时才接，避免把短行、列表项误接。
 */
export function continuesAcrossPage(prev: TextItem, next: TextItem): boolean {
  if (prev.kind !== 'paragraph' || next.kind !== 'paragraph') return false
  if (next.page < prev.page) return false
  if (next.page === prev.page && prev.text.trim().length < 80) return false
  if (SENTENCE_END.test(prev.text)) return false
  if (/^\s*\p{Ll}/u.test(next.text)) return true
  return prev.text.trim().length >= 80 && crossesPageOrColumn(prev, next) && /^\s*\p{Lu}\p{Ll}+[\s,]/u.test(next.text)
}

export function buildDoc(items: TextItem[], outline: OutlineItem[], opts: { showNoise: boolean; raw?: boolean }): TextDoc {
  const levels = headingLevels(outline)
  const split = opts.raw ? { front: null, abstract: [] as TextItem[], rest: items } : splitFront(items)
  const blocks: Block[] = []
  const references: TextItem[] = []
  let hidden = 0
  let lastSection: string | null = null
  let lastPage = 0
  // 目录里每个标题的位置，以及哪些标题下面有正文（没有的是“空父标题”，要补上）
  // 章节一律按 id 认（旧后端没给 section_id 时退回标题）：同名小节不能互相顶替
  const keyOf = (i: { section_id?: string | null; section_title: string | null }): string | null => secKey(i.section_id) ?? i.section_title
  const outlineIdx = new Map<string, number>()
  outline.forEach((o, i) => {
    for (const k of [secKey(o.id), o.title]) if (k && !outlineIdx.has(k)) outlineIdx.set(k, i)
  })
  const hasText = new Set(items.map(keyOf).filter((t): t is string => !!t))
  const outlineKey = (o: OutlineItem): string => secKey(o.id) ?? o.title
  let lastIdx: number | null = null
  // 序号就是阅读顺序：解析时图、表已经挪到首次提到它的段落后面（页码因此不一定单调），不能再按页码重排
  const sorted = [...split.rest].sort((a, b) => a.ord - b.ord)
  for (const it of sorted) {
    if (it.kind === 'reference') {
      references.push(it)
      continue
    }
    if (it.kind === 'figure_text' && !opts.showNoise) {
      hidden += 1
      continue
    }
    // 挪过位置的图/表注不标页码，也不改变“当前页”（否则正文里会冒出一个回头的页码）
    const floating = it.kind === 'caption' || it.kind === 'figure_text'
    let pageStart = !floating && it.page !== lastPage
    if (!floating) lastPage = it.page
    const curKey = keyOf(it)
    if (it.section_title && curKey && curKey !== lastSection) {
      lastSection = curKey
      if (it.section_title !== IMPLICIT_BODY) {
        // 补上夹在上一个标题和这个标题之间、自己下面没有任何正文的标题（“2 Background”下面直接就是“2.1 …”）：
        // 标题只在遇到某一节的第一段正文时才会出现，这类父标题不补的话，右边的“本文结构”就会比左边的目录少。
        const k = outlineIdx.get(curKey)
        if (k !== undefined && lastIdx !== null && k > lastIdx + 1) {
          for (let j = lastIdx + 1; j < k; j++) {
            const o = outline[j]
            if (!hasText.has(outlineKey(o)) && o.title !== IMPLICIT_BODY) {
              blocks.push({ type: 'heading', key: `o${o.id}`, title: o.title, level: levels.get(o.title) ?? 2, page: o.page, secKey: secKey(o.id) })
            }
          }
        }
        if (k !== undefined) lastIdx = k
        blocks.push({ type: 'heading', key: `h${it.ord}`, title: it.section_title, level: levels.get(it.section_title) ?? 2, page: it.page, secKey: secKey(it.section_id) })
        pageStart = false // 页码标在标题上，不重复标在紧随其后的段落
      }
    }
    const prev = blocks[blocks.length - 1]
    if (!opts.raw && prev?.type === 'passage' && continuesAcrossPage(prev.parts[prev.parts.length - 1], it)) {
      prev.parts.push(it)
      continue
    }
    blocks.push({ type: 'passage', item: it, parts: [it], pageStart })
  }
  return { front: split.front, abstract: split.abstract, blocks, references, hidden }
}

/** 一个块的显示文字：被页边界切开的段落接成一段（行末连字符一并处理）。 */
export function blockText(parts: TextItem[]): string {
  return parts.length === 1 ? parts[0].text : joinParts(parts.map((p) => p.text))
}

/** 第一个页码不小于 page 的段落（页上没有正文时顺延到后面）。 */
export function firstOrdOnPage(items: TextItem[], page: number): number | null {
  const hit = items.find((i) => i.page === page && i.kind !== 'reference') ?? items.find((i) => i.page >= page)
  return hit ? hit.ord : null
}

function overlap(a: BBox, b: BBox): number {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0])
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1])
  return w > 0 && h > 0 ? w * h : 0
}

/** 证据只带“页码 + 位置框”时，找出文字里对应的段落：重叠面积最大者；都不重叠则取垂直方向最近者。 */
export function findPassageByBBox(items: TextItem[], page: number, bbox: BBox | null | undefined): number | null {
  const onPage = items.filter((i) => i.page === page)
  if (onPage.length === 0) return firstOrdOnPage(items, page)
  if (!bbox) return onPage[0].ord
  let best: { ord: number; score: number } | null = null
  for (const it of onPage) {
    const s = overlap(it.bbox, bbox)
    if (s > 0 && (!best || s > best.score)) best = { ord: it.ord, score: s }
  }
  if (best) return best.ord
  const cy = (bbox[1] + bbox[3]) / 2
  let near = onPage[0]
  let dist = Infinity
  for (const it of onPage) {
    const d = Math.abs((it.bbox[1] + it.bbox[3]) / 2 - cy)
    if (d < dist) {
      dist = d
      near = it
    }
  }
  return near.ord
}

export interface Seg {
  text: string
  /** 原文里加粗的文字 */
  bold?: boolean
  /** 原文里斜体的文字 */
  italic?: boolean
  /** 这段文字是一个行内公式：text 是提取出的原文，tex 是还原出的 LaTeX，渲染时用公式代替 */
  tex?: string
  /** 搜索词命中 */
  mark?: boolean
  /** 术语表里的词（第一次出现的位置）：值是术语原文 */
  term?: string
  /** 用户高亮（来自批注） */
  annId?: string
  color?: string
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 把查询拆成词（忽略过短的词），用于在文字里高亮。 */
export function queryTerms(query: string): string[] {
  return query
    .trim()
    .split(/\s+/)
    .filter((t) => t.length >= 2 || /[一-鿿]/.test(t))
}

export function highlightTerms(text: string, query: string): Seg[] {
  const terms = queryTerms(query)
  if (terms.length === 0) return [{ text }]
  const re = new RegExp(terms.map(escapeRe).join('|'), 'gi')
  const out: Seg[] = []
  let last = 0
  for (const m of text.matchAll(re)) {
    const s = m.index ?? 0
    if (m[0].length === 0) continue
    if (s > last) out.push({ text: text.slice(last, s) })
    out.push({ text: m[0], mark: true })
    last = s + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out.length ? out : [{ text }]
}

export interface AnnMark {
  id: string
  text: string
  color: string
}

/** 把用户的高亮按“原文逐字匹配”标到段落文字上；匹配不到的（跨段、连字符差异）不标，如实不显示。 */
export function markAnnotations(text: string, marks: AnnMark[]): Seg[] {
  const found: Array<{ s: number; e: number; m: AnnMark }> = []
  for (const m of marks) {
    const needle = m.text.trim()
    if (needle.length < 2) continue
    const s = text.indexOf(needle)
    if (s >= 0) found.push({ s, e: s + needle.length, m })
  }
  if (found.length === 0) return [{ text }]
  found.sort((a, b) => a.s - b.s)
  const out: Seg[] = []
  let last = 0
  for (const f of found) {
    if (f.s < last) continue
    if (f.s > last) out.push({ text: text.slice(last, f.s) })
    out.push({ text: text.slice(f.s, f.e), annId: f.m.id, color: f.m.color })
    last = f.e
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out
}

/** 一段文字的最终分段：先标用户高亮，再在其余部分标搜索词。 */
export function segmentsFor(text: string, marks: AnnMark[], query: string): Seg[] {
  const base = markAnnotations(text, marks)
  if (!queryTerms(query).length) return base
  return base.flatMap((seg) => (seg.annId ? [seg] : highlightTerms(seg.text, query)))
}

/** 把解析时记下的“原文加粗片段”按顺序定位到显示文字里；找不到的片段直接跳过（宁可少加粗，不加错）。 */
export function boldRanges(text: string, runs: string[] | undefined): Array<[number, number]> {
  const out: Array<[number, number]> = []
  let cur = 0
  for (const r of runs ?? []) {
    if (!r) continue
    const i = text.indexOf(r, cur)
    if (i < 0) continue
    out.push([i, i + r.length])
    cur = i + r.length
  }
  return out
}

/** 在已有的分段（搜索命中、批注高亮）上叠加加粗：只切开，不改变文字内容。 */
export function applyBold(segs: Seg[], ranges: Array<[number, number]>): Seg[] {
  return applyFlag(segs, ranges, 'bold')
}

/** 同 applyBold，叠加斜体。 */
export function applyItalic(segs: Seg[], ranges: Array<[number, number]>): Seg[] {
  return applyFlag(segs, ranges, 'italic')
}

function applyFlag(segs: Seg[], ranges: Array<[number, number]>, key: 'bold' | 'italic'): Seg[] {
  if (ranges.length === 0) return segs
  const out: Seg[] = []
  let pos = 0
  for (const s of segs) {
    const end = pos + s.text.length
    const cuts = new Set<number>([0, s.text.length])
    for (const [a, b] of ranges) {
      if (a > pos && a < end) cuts.add(a - pos)
      if (b > pos && b < end) cuts.add(b - pos)
    }
    const pts = [...cuts].sort((x, y) => x - y)
    for (let i = 0; i + 1 < pts.length; i++) {
      const from = pts[i]
      const to = pts[i + 1]
      const mid = pos + from
      const on = ranges.some(([a, b]) => mid >= a && mid < b)
      out.push(on ? { ...s, text: s.text.slice(from, to), [key]: true } : { ...s, text: s.text.slice(from, to) })
    }
    pos = end
  }
  return out
}

/** 行内公式：把解析时还原的 {src, tex} 按顺序定位到显示文字里。很短的片段（单个变量）要求前后不是字母数字，避免命中词中间的字母；找不到的跳过。 */
export function mathRanges(text: string, runs: Array<{ src: string; tex: string }> | undefined): Array<[number, number, string]> {
  const out: Array<[number, number, string]> = []
  const alnum = /[\p{L}\p{N}]/u
  let cur = 0
  for (const r of runs ?? []) {
    if (!r.src || !r.tex) continue
    let from = cur
    for (;;) {
      const i = text.indexOf(r.src, from)
      if (i < 0) break
      const before = i > 0 ? text[i - 1] : ''
      const after = text[i + r.src.length] ?? ''
      const head = alnum.test(r.src[0])
      const tail = alnum.test(r.src[r.src.length - 1])
      if ((head && alnum.test(before)) || (tail && alnum.test(after))) {
        from = i + 1
        continue
      }
      out.push([i, i + r.src.length, r.tex])
      cur = i + r.src.length
      break
    }
  }
  return out
}

/** 把行内公式叠加到分段上：只替换完整落在同一个分段里的公式（搜索命中、批注高亮把公式切开的那种不处理，保持原文字）。 */
export function applyMath(segs: Seg[], ranges: Array<[number, number, string]>): Seg[] {
  if (ranges.length === 0) return segs
  const out: Seg[] = []
  let pos = 0
  for (const s of segs) {
    const end = pos + s.text.length
    let at = 0
    for (const [a, b, tex] of ranges) {
      if (a < pos || b > end || s.tex) continue
      if (a - pos > at) out.push({ ...s, text: s.text.slice(at, a - pos) })
      out.push({ ...s, text: s.text.slice(a - pos, b - pos), tex })
      at = b - pos
    }
    if (at === 0) out.push(s)
    else if (at < s.text.length) out.push({ ...s, text: s.text.slice(at) })
    pos = end
  }
  return out
}

/** 术语在正文里的匹配：英文词按词边界，全大写的缩写区分大小写，其余忽略大小写；符号之类的按原样。 */
export function termRegExp(t: string): RegExp {
  const word = /^[A-Za-z0-9][A-Za-z0-9 \-]*$/.test(t)
  const flags = t === t.toUpperCase() && /[A-Z]{2,}/.test(t) ? '' : 'i'
  return new RegExp(word ? `(?<![A-Za-z0-9])${escapeRe(t)}(?![A-Za-z0-9])` : escapeRe(t), flags)
}

/**
 * 在显示文字里标出术语表里的词（只标每个词在这一段里的第一次出现）：不改变文字内容，只把它切成单独的分段。
 * 英文词按词边界匹配（缩写区分大小写，其余忽略大小写）；已经是公式、批注、搜索命中的分段不动。
 */
export function applyTerms(segs: Seg[], terms: string[]): Seg[] {
  if (terms.length === 0) return segs
  const done = new Set<string>()
  const out: Seg[] = []
  for (const s of segs) {
    if (s.tex || s.annId || s.mark || s.term) {
      out.push(s)
      continue
    }
    let rest = s
    let guard = 0
    while (rest.text && guard++ < 40) {
      let best: { i: number; len: number; term: string } | null = null
      for (const t of terms) {
        if (done.has(t)) continue
        const m = termRegExp(t).exec(rest.text)
        if (m && (best === null || m.index < best.i || (m.index === best.i && t.length > best.len))) best = { i: m.index, len: t.length, term: t }
      }
      if (!best) break
      if (best.i > 0) out.push({ ...rest, text: rest.text.slice(0, best.i) })
      out.push({ ...rest, text: rest.text.slice(best.i, best.i + best.len), term: best.term })
      done.add(best.term)
      rest = { ...rest, text: rest.text.slice(best.i + best.len) }
    }
    if (rest.text) out.push(rest)
  }
  return out
}

/** 导出当前视图的纯文本：页与页之间用“—— 第 N 页 ——”分隔；参考文献放最后。 */
export function buildPlain(items: TextItem[], trans: Map<number, string>, view: TextView, opts: { showNoise: boolean }): string {
  const body = items.filter((i) => i.kind !== 'reference' && (opts.showNoise || i.kind !== 'figure_text'))
  const refs = items.filter((i) => i.kind === 'reference')
  const out: string[] = []
  let page = 0
  const emit = (it: TextItem): void => {
    if (it.page !== page) {
      page = it.page
      out.push(`—— 第 ${page} 页 ——`)
    }
    const t = trans.get(it.ord)
    if (view === 'text') out.push(it.text)
    else if (view === 'target') out.push(t ?? it.text)
    else out.push(t ? `${it.text}\n${t}` : it.text)
  }
  body.forEach(emit)
  if (refs.length) {
    out.push('—— 参考文献 ——')
    for (const r of refs) out.push(r.text)
  }
  return out.join('\n\n')
}

/** 当前页：最后一个“顶部已经越过阅读线”的页（tops 为各页分节相对容器顶部的位置，按页码升序）。 */
export function pageAtLine(tops: Array<{ page: number; top: number }>, line: number): number {
  let cur = tops.length ? tops[0].page : 1
  for (const t of tops) {
    if (t.top <= line) cur = t.page
    else break
  }
  return cur
}

export const TEXT_SIZES = [14, 15, 16, 17, 18, 20, 22]
export function stepTextSize(size: number, dir: 1 | -1): number {
  const i = TEXT_SIZES.findIndex((s) => s >= size)
  const idx = Math.max(0, Math.min(TEXT_SIZES.length - 1, (i < 0 ? TEXT_SIZES.length - 1 : i) + dir))
  return TEXT_SIZES[idx]
}


/** PDF 页面上的一点（PDF 坐标）落在哪个段落里：先找包含这个点的段落，找不到就取这一页里离它最近的。 */
export function passageAtPoint(items: TextItem[], page: number, x: number, y: number, maxDist = Infinity): TextItem | null {
  let best: TextItem | null = null
  let bestD = Infinity
  for (const it of items) {
    if (it.page !== page || !it.bbox) continue
    const [x0, y0, x1, y1] = it.bbox
    if (x >= x0 && x <= x1 && y >= y0 && y <= y1) return it
    const dx = x < x0 ? x0 - x : x > x1 ? x - x1 : 0
    const dy = y < y0 ? y0 - y : y > y1 ? y - y1 : 0
    const d = Math.hypot(dx, dy)
    if (d < bestD && d <= maxDist) {
      bestD = d
      best = it
    }
  }
  return best
}
