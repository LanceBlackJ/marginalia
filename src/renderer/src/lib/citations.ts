/**
 * AI 回答中的引用标记解析：
 * - 正文：`[P12]`、`[P12][P30]`、`[P12, P30]`、`[P12,P30,P41]`
 * - 补充材料：`[S1P12]`（第 1 个补充文档的第 12 段），可与正文引用混写，如 `[P3, S1P12]`
 * 相邻（仅隔空白）的引用合并为一组。
 */

export type CiteSegment = { type: 'text'; text: string } | { type: 'cite'; ords: number[]; raw: string }

const CITE_RE = /\[\s*P\s*\d+(?:\s*[,，;；、]\s*P?\s*\d+)*\s*\]/g

function ordsOf(token: string): number[] {
  const out: number[] = []
  for (const m of token.matchAll(/\d+/g)) out.push(Number(m[0]))
  return out
}

function dedupe(ords: number[]): number[] {
  return ords.filter((o, i) => ords.indexOf(o) === i)
}

/** 只解析正文引用（[P12]）。含补充材料引用的文本请用 parseRefSegments。 */
export function parseCitations(text: string): CiteSegment[] {
  const segments: CiteSegment[] = []
  let last = 0
  for (const m of text.matchAll(CITE_RE)) {
    const idx = m.index ?? 0
    const between = text.slice(last, idx)
    const prev = segments[segments.length - 1]
    if (prev && prev.type === 'cite' && between.trim() === '' && between.length <= 2 && !between.includes('\n')) {
      // 与上一个引用相邻：合并
      prev.ords = dedupe([...prev.ords, ...ordsOf(m[0])])
      prev.raw += between + m[0]
    } else {
      if (between) segments.push({ type: 'text', text: between })
      segments.push({ type: 'cite', ords: dedupe(ordsOf(m[0])), raw: m[0] })
    }
    last = idx + m[0].length
  }
  if (last < text.length) segments.push({ type: 'text', text: text.slice(last) })
  return segments
}

/** 文本中出现过的全部正文段落序号（按出现顺序去重）。 */
export function extractOrds(text: string): number[] {
  const all: number[] = []
  for (const seg of parseCitations(text)) if (seg.type === 'cite') all.push(...seg.ords)
  return dedupe(all)
}

// ---------------- 含补充材料的引用 ----------------

/** 一个引用：doc=0 表示正文，doc=n 表示第 n 个补充文档。 */
export interface CiteRef {
  doc: number
  ord: number
}

export type RefSegment = { type: 'text'; text: string } | { type: 'cite'; refs: CiteRef[]; raw: string }

const REF_TOKEN = '(?:S\\d+\\s*P|P)\\s*\\d+'
const REF_RE = new RegExp(`\\[\\s*${REF_TOKEN}(?:\\s*[,，;；、]\\s*(?:${REF_TOKEN}|\\d+))*\\s*\\]`, 'g')

export function refTag(r: CiteRef): string {
  return r.doc === 0 ? `P${r.ord}` : `S${r.doc}P${r.ord}`
}

export function sameRef(a: CiteRef, b: CiteRef): boolean {
  return a.doc === b.doc && a.ord === b.ord
}

function dedupeRefs(refs: CiteRef[]): CiteRef[] {
  return refs.filter((r, i) => refs.findIndex((x) => sameRef(x, r)) === i)
}

/** 解析一个括号内的引用串（不含方括号）；裸数字沿用前一个引用的文档。 */
function refsOf(inner: string): CiteRef[] {
  const out: CiteRef[] = []
  let doc = 0
  for (const part of inner.split(/[,，;；、]/)) {
    const m = /^\s*(?:S(\d+)\s*)?(P)?\s*(\d+)\s*$/.exec(part)
    if (!m) continue
    if (m[2]) doc = m[1] ? Number(m[1]) : 0
    out.push({ doc, ord: Number(m[3]) })
  }
  return out
}

export function parseRefSegments(text: string): RefSegment[] {
  const segments: RefSegment[] = []
  let last = 0
  for (const m of text.matchAll(REF_RE)) {
    const idx = m.index ?? 0
    const between = text.slice(last, idx)
    const refs = refsOf(m[0].slice(1, -1))
    const prev = segments[segments.length - 1]
    if (prev && prev.type === 'cite' && between.trim() === '' && between.length <= 2 && !between.includes('\n')) {
      prev.refs = dedupeRefs([...prev.refs, ...refs])
      prev.raw += between + m[0]
    } else {
      if (between) segments.push({ type: 'text', text: between })
      segments.push({ type: 'cite', refs: dedupeRefs(refs), raw: m[0] })
    }
    last = idx + m[0].length
  }
  if (last < text.length) segments.push({ type: 'text', text: text.slice(last) })
  return segments
}

/** 把引用标记替换成 Markdown 链接，交给 react-markdown 的 a 组件渲染为引用 chip。
 * 只有正文引用时沿用旧格式 `[P12, P30](cite:12,30)`；含补充材料时用 `[P12, S1P3](cite:P12,S1P3)`。 */
export function citationsToMarkdownLinks(text: string): string {
  return parseRefSegments(text)
    .map((seg) => {
      if (seg.type === 'text') return seg.text
      const label = seg.refs.map(refTag).join(', ')
      const legacy = seg.refs.every((r) => r.doc === 0)
      const href = legacy ? seg.refs.map((r) => r.ord).join(',') : seg.refs.map(refTag).join(',')
      return `[${label}](cite:${href})`
    })
    .join('')
}

export function parseCiteHref(href: string | undefined): number[] | null {
  if (!href || !href.startsWith('cite:')) return null
  const ords = href
    .slice(5)
    .split(',')
    .filter((s) => /^\d+$/.test(s))
    .map((s) => Number(s))
  return ords.length > 0 ? ords : null
}

/** 解析 cite: 链接，同时支持旧格式（纯数字，正文）与新格式（P12 / S1P3）。 */
export function parseCiteHrefRefs(href: string | undefined): CiteRef[] | null {
  if (!href || !href.startsWith('cite:')) return null
  const refs: CiteRef[] = []
  for (const part of href.slice(5).split(',')) {
    const m = /^(?:S(\d+)P|P)?(\d+)$/.exec(part)
    if (!m) continue
    refs.push({ doc: m[1] ? Number(m[1]) : 0, ord: Number(m[2]) })
  }
  return refs.length > 0 ? refs : null
}
