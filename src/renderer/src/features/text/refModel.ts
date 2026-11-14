/** 参考文献的纯函数：引用数字拆分、按标记切分文字、引文格式、浮层定位。 */
import type { CitationMarker, RefItem, RefResolution } from '@/lib/types.references'

export interface RefPiece {
  text: string
  /** 有 refs 的片段是可点击的引用标记；没有的是普通文字 */
  refs?: number[]
}

export interface ParaRefs {
  /** 上标数字里后端确认过的编号 */
  sup: ReadonlySet<number>
  /** 方括号、“refs 3”、作者-年份这类按文字定位的标记 */
  text: readonly CitationMarker[]
  /** 译文用：后端没有标过位置的方括号编号（译文里破折号、括号、逗号常被改写），按这些确实存在的编号再认一遍 */
  loose?: ReadonlySet<number>
  /** 译文：数字贴在汉字后面的上标角标（“……能力1–3。”）也按 sup 里的编号认 */
  zh?: boolean
}

/** 一个显示块可能由几个相邻段落拼成：把它们各自的引用信息合并起来；都没有就返回 null。 */
export function mergeParaRefs(ords: readonly number[], map: ReadonlyMap<number, ParaRefs>): ParaRefs | null {
  const found = ords.map((o) => map.get(o)).filter((x): x is ParaRefs => !!x)
  if (found.length === 0) return null
  if (found.length === 1) return found[0]
  return { sup: new Set(found.flatMap((f) => [...f.sup])), text: found.flatMap((f) => [...f.text]) }
}

const RANGE_MAX = 30

/** “1,2”“14–18”“3, 5-7” → 编号列表（区间最长 30，超出的丢弃）。 */
export function expandNumbers(digits: string): number[] {
  const out: number[] = []
  for (const part of digits.split(/\s*,\s*/)) {
    const m = /^(\d{1,3})\s*[–‐-]\s*(\d{1,3})$/.exec(part)
    if (m) {
      const a = Number(m[1])
      const b = Number(m[2])
      if (a < b && b - a <= RANGE_MAX) for (let n = a; n <= b; n++) out.push(n)
    } else if (/^\d{1,3}$/.test(part)) out.push(Number(part))
  }
  return [...new Set(out)]
}

/**
 * 把上标数字（“1,2”“3,8,14–18”）拆成可点击的片段：逗号分开的每个编号各自可点；区间（14–18）是一个整体，
 * 点开后可以在区间内切换。只有后端确认存在的编号（valid）才可点击，其余保持普通文字。
 */
export function splitSupDigits(digits: string, valid: ReadonlySet<number>): RefPiece[] {
  const out: RefPiece[] = []
  const parts = digits.split(/(\s*,\s*)/)
  for (const part of parts) {
    if (!part) continue
    if (/^\s*,\s*$/.test(part)) {
      out.push({ text: part })
      continue
    }
    const nums = expandNumbers(part).filter((n) => valid.has(n))
    out.push(nums.length > 0 ? { text: part, refs: nums } : { text: part })
  }
  return out
}

const LOOSE_BRACKET = /[[［【]\s*(\d{1,3}(?:\s*[,，、–—‐‑‒−~～－-]\s*\d{1,3}){0,8})\s*[\]］】]/g

/**
 * 译文里的方括号引用：译文里的“[64–66]”常被改成“[64-66]”“［64—66］”“【64】”，后端按原文记下的标记文字对不上。
 * 这里不依赖标记文字，只要括号里的编号在参考文献列表里确实存在，就当作引用（不存在的不处理，不会误加链接）。
 */
export function splitLooseBrackets(text: string, valid: ReadonlySet<number>): RefPiece[] {
  const out: RefPiece[] = []
  let cursor = 0
  for (const m of text.matchAll(LOOSE_BRACKET)) {
    const group = m[1].replace(/[，、]/g, ',').replace(/[–—‐‑‒−~～－]/g, '-')
    const nums = expandNumbers(group).filter((n) => valid.has(n))
    if (nums.length === 0) continue
    const at = m.index ?? 0
    if (at > cursor) out.push({ text: text.slice(cursor, at) })
    out.push({ text: m[0], refs: nums })
    cursor = at + m[0].length
  }
  if (out.length === 0) return [{ text }]
  if (cursor < text.length) out.push({ text: text.slice(cursor) })
  return out
}

/** 按后端给的标记文字，把一段显示文字切成“普通文字 / 可点击标记”。找不到的标记不处理（显示文字可能被整理过）。 */
export function splitByMarkers(text: string, markers: readonly CitationMarker[]): RefPiece[] {
  if (markers.length === 0) return [{ text }]
  const out: RefPiece[] = []
  let cursor = 0
  for (const mk of [...markers].sort((a, b) => a.start - b.start)) {
    let at = -1
    if (mk.pre) {
      const k = text.indexOf(mk.pre + mk.text, Math.max(0, cursor - mk.pre.length))
      if (k >= 0) at = k + mk.pre.length
    }
    if (at < cursor) at = text.indexOf(mk.text, cursor)
    if (at < 0) continue
    if (at > cursor) out.push({ text: text.slice(cursor, at) })
    out.push({ text: mk.text, refs: mk.refs })
    cursor = at + mk.text.length
  }
  if (cursor === 0 && out.length === 0) return [{ text }]
  if (cursor < text.length) out.push({ text: text.slice(cursor) })
  return out
}

function firstAuthorFamily(authors: readonly string[]): string {
  const a = authors.find((x) => x && x !== 'et al.')
  return a ? a.split(',')[0].trim() : ''
}

/** 一行引文文本：作者（年份）。标题。期刊。优先用联网查到的题录，没有就用解析出的字段，再没有就用原文。 */
export function plainCitation(ref: RefItem, res?: RefResolution | null): string {
  if (res?.found && res.title) {
    const au = (res.authors ?? []).slice(0, 6).join(', ') + ((res.authors?.length ?? 0) > 6 ? ' et al.' : '')
    return [au, res.year ? `(${res.year})` : '', `${res.title}.`, res.venue ? `${res.venue}.` : '', res.doi ? `https://doi.org/${res.doi}` : '']
      .filter(Boolean)
      .join(' ')
  }
  if (ref.title) {
    return [ref.authors.join(', '), ref.year ? `(${ref.year})` : '', `${ref.title}.`, ref.venue ? `${ref.venue}.` : '', ref.url].filter(Boolean).join(' ')
  }
  return ref.raw
}

const stripBraces = (s: string): string => s.replace(/[{}\\]/g, '')

/** BibTeX：只写确实有的字段；键为“第一作者姓+年份+标题首词”。 */
export function bibtex(ref: RefItem, res?: RefResolution | null): string {
  const found = !!(res?.found && res.title)
  const title = found ? res!.title! : ref.title
  const authors = found ? res!.authors ?? [] : ref.authors.filter((a) => a !== 'et al.')
  const year = found ? res!.year ?? ref.year : ref.year
  const venue = found ? res!.venue ?? '' : ref.venue
  const doi = (found ? res!.doi : '') || ref.doi
  const family = (authors[0] ? (authors[0].includes(',') ? authors[0].split(',')[0] : authors[0].split(' ').pop() ?? '') : firstAuthorFamily(ref.authors)) || 'ref'
  const word = (title.match(/[A-Za-z]{4,}/)?.[0] ?? 'untitled').toLowerCase()
  const key = `${family}${year ?? ''}${word}`.replace(/[^A-Za-z0-9]/g, '')
  const fields: Array<[string, string]> = [
    ['title', title],
    ['author', authors.join(' and ')],
    ['year', year ? String(year) : ''],
    ['journal', venue],
    ['doi', doi],
    ['note', title ? '' : ref.raw]
  ]
  const body = fields
    .filter(([, v]) => v)
    .map(([k, v]) => `  ${k} = {${stripBraces(v)}}`)
    .join(',\n')
  return `@article{${key},\n${body}\n}`
}

export interface Placement {
  left: number
  top?: number
  bottom?: number
  maxHeight: number
}

/** 浮层定位：贴着标记，优先放下方，空间不够放上方；左右夹在视口内。 */
export function placePopover(rect: { left: number; right: number; top: number; bottom: number }, width: number, viewport: { w: number; h: number }, height = 320): Placement {
  const center = (rect.left + rect.right) / 2
  const left = Math.min(Math.max(center - width / 2, 8), Math.max(8, viewport.w - width - 8))
  const below = viewport.h - rect.bottom - 12
  const above = rect.top - 12
  if (below >= Math.min(height, 240) || below >= above) return { left, top: rect.bottom + 6, maxHeight: Math.max(160, below - 6) }
  return { left, bottom: viewport.h - rect.top + 6, maxHeight: Math.max(160, above - 6) }
}

export function matchLabel(level: 'high' | 'medium' | 'low'): string {
  return level === 'high' ? '匹配可信度高' : level === 'medium' ? '匹配可信度中等' : '匹配可信度低：可能不是同一篇，请对照原文'
}

/** 这条参考文献在界面里的标题行：优先解析出的标题，没有就用原文开头。 */
export function refHeadline(ref: RefItem): string {
  return ref.title || ref.raw.replace(/^\s*\d+[.\]]?\s*/, '').slice(0, 110)
}

// 译文里的上标角标：数字直接贴在汉字或全角标点后面（“……的能力1–3。”），后面是句末标点、空白或结尾
const ZH_SUP = /(\d{1,3}(?:\s*[,，、]\s*\d{1,3}|\s*[–—-]\s*\d{1,3}){0,6})(?=[。，；：！？、）」”）(（【《〈「『\s—–-]|$)/g
// 数字前面是这些字时，数字是“图 2”“第 3 节”“式 5”这类编号，不是引用角标
const NOT_CITE_BEFORE = /[图表式节章第项条段附录例组号版步轮]$/

/**
 * 译文里的引用角标：原文的上标数字（“…occur1–3.”）翻成中文后，数字还贴在汉字后面，没有上标样式也没有链接。
 * 只有这段原文里后端确认过的参考文献编号（valid）才认，并且数字要贴在汉字或全角标点后面——
 * “提高了 5 倍”“共 3 个”（后面是汉字）、“图 2”“第 3 节”（前面是编号字）都不会被当成角标。
 */
export function splitCjkSup(text: string, valid: ReadonlySet<number>): RefPiece[] {
  if (valid.size === 0) return [{ text }]
  const out: RefPiece[] = []
  let cursor = 0
  for (const m of text.matchAll(ZH_SUP)) {
    const at = m.index ?? 0
    const before = text.slice(0, at)
    if (!/[　-〿一-鿿＀-￯]$/.test(before) || NOT_CITE_BEFORE.test(before)) continue
    const group = m[1].replace(/[，、]/g, ',').replace(/[—]/g, '-')
    const nums = expandNumbers(group)
    if (nums.length === 0 || !nums.every((n) => valid.has(n))) continue
    if (at > cursor) out.push({ text: text.slice(cursor, at) })
    out.push({ text: m[1], refs: nums })
    cursor = at + m[1].length
  }
  if (out.length === 0) return [{ text }]
  if (cursor < text.length) out.push({ text: text.slice(cursor) })
  return out
}
