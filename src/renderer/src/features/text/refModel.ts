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
  return {
    sup: new Set(found.flatMap((f) => [...f.sup])),
    text: found.flatMap((f) => [...f.text]),
    loose: new Set(found.flatMap((f) => [...(f.loose ?? [])])),
    zh: found.some((f) => f.zh)
  }
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

function flexibleCitationPattern(value: string): RegExp {
  const parts: string[] = []
  for (const ch of value) {
    if (/\s/.test(ch)) parts.push('\\s*')
    else if (ch === '(') parts.push('[（(]')
    else if (ch === ')') parts.push('[）)]')
    else if (ch === ';') parts.push('[;；]')
    else if (ch === ',') parts.push('[,，]')
    else if (ch === '–' || ch === '—' || ch === '-') parts.push('[–—-]')
    else parts.push(ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  }
  return new RegExp(parts.join(''), 'g')
}

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
  for (const mk of [...markers].sort((a, b) => a.start - b.start || b.end - a.end)) {
    // 后端会把同一括号内的多篇引用归并成一个完整标记。这里再做一道防御，
    // 即使旧缓存或某个 PDF 的内部链接仍带有嵌套标记，也不能把内层标记
    // 错定位到后面的正文。
    // 某些旧数据没有保存 start/end（默认都是 0），这类标记仍需按文字顺序定位。
    if (mk.end > mk.start && mk.start < cursor && mk.end <= cursor) continue
    if (mk.end > mk.start && mk.start < cursor && mk.end > cursor) continue
    let at = -1
    let matchedEnd = -1
    if (mk.pre) {
      const k = text.indexOf(mk.pre + mk.text, Math.max(0, cursor - mk.pre.length))
      if (k >= 0) {
        at = k + mk.pre.length
        matchedEnd = at + mk.text.length
      }
    }
    if (at < cursor) {
      at = text.indexOf(mk.text, cursor)
      if (at >= 0) matchedEnd = at + mk.text.length
    }
    // 译文可能把引用外侧括号/分号换成全角，或在中文和引用之间增删空格；
    // 引用本身仍来自后端确认的同一组文献，因此用等价标点和可选空格再定位一次。
    if (at < 0) {
      const flexible = flexibleCitationPattern(mk.text)
      flexible.lastIndex = cursor
      const hit = flexible.exec(text)
      if (hit) {
        at = hit.index
        matchedEnd = hit.index + hit[0].length
      }
    }
    if (at < 0) continue
    if (at > cursor) out.push({ text: text.slice(cursor, at) })
    out.push({ text: text.slice(at, matchedEnd), refs: mk.refs })
    cursor = matchedEnd
  }
  if (cursor === 0 && out.length === 0) return [{ text }]
  if (cursor < text.length) out.push({ text: text.slice(cursor) })
  return out
}

/**
 * 在已经带有格式/搜索/批注分段的文字上定位引用。
 * 引用匹配必须看完整段文字，之后再把命中的范围切回原分段；否则一组引用
 * 恰好跨过一个格式边界时，逐段匹配会把它误当成普通文字。
 */
export function splitSegmentsByMarkers<T extends { text: string }>(
  segments: readonly T[],
  markers: readonly CitationMarker[]
): Array<{ segment: T; text: string; refs?: number[] }> | null {
  if (segments.length <= 1 || markers.length === 0) return null
  const whole = segments.map((s) => s.text).join('')
  const marked = splitByMarkers(whole, markers)
  if (!marked.some((p) => p.refs)) return null
  const starts: number[] = []
  let end = 0
  for (const s of segments) {
    starts.push(end)
    end += s.text.length
  }
  const out: Array<{ segment: T; text: string; refs?: number[] }> = []
  let pieceStart = 0
  for (const piece of marked) {
    const pieceEnd = pieceStart + piece.text.length
    for (let i = 0; i < segments.length; i++) {
      const segStart = starts[i]
      const segEnd = segStart + segments[i].text.length
      const from = Math.max(pieceStart, segStart)
      const to = Math.min(pieceEnd, segEnd)
      if (to > from) out.push({ segment: segments[i], text: segments[i].text.slice(from - segStart, to - segStart), refs: piece.refs })
    }
    pieceStart = pieceEnd
  }
  return out
}

/**
 * citations 接口暂时没有返回标记时的前端兜底：只在括号内容同时命中已确认题录的
 * 第一作者姓氏和年份（含 a/b 后缀）时建立整组引用，不凭单独的数字猜测引用。
 */
export function inferAuthorYearMarkers(text: string, refs: readonly RefItem[]): CitationMarker[] {
  if (!text || refs.length === 0) return []
  const out: CitationMarker[] = []
  for (const group of text.matchAll(/[（(][^()（）]*[）)]/g)) {
    const value = group[0]
    const body = value.slice(1, -1)
    const matched: number[] = []
    // 一个大括号里可能有多篇文献。按分号逐项匹配，不能让某篇文献的
    // 作者跨到下一篇文献的年份，否则会把“作者和年份不在同一项”的
    // 普通文字误判成引用。
    for (const item of body.split(/[;；]+/)) {
      const yearMatch = /(?:^|[^\d])((?:19|20)\d{2})([a-z]?)(?=$|[^\da-z])/i.exec(item)
      if (!yearMatch || yearMatch.index === undefined) continue
      const year = Number(yearMatch[1])
      const suffix = yearMatch[2].toLowerCase()
      const beforeYear = item.slice(0, yearMatch.index + yearMatch[0].length - yearMatch[1].length - yearMatch[2].length)
      const candidates = refs.filter((ref) => {
        if (!ref.year || ref.year !== year) return false
        const author = ref.authors.find((a) => a && a.toLowerCase() !== 'et al.') ?? ''
        const family = authorFamily(author)
        if (!family) return false
        const refYear = citationYear(ref)
        if (refYear.suffix !== suffix) return false
        return new RegExp(`\\b${escapeRegExp(family)}\\b`, 'i').test(beforeYear)
      })
      // 同一第一作者、同一年份且后缀相同的条目无法仅凭正文消歧，
      // 与后端规则保持一致，宁可不建立错误链接。
      if (candidates.length === 1) matched.push(candidates[0].index)
    }
    if (matched.length > 0) {
      const start = group.index ?? 0
      out.push({ kind: 'authoryear', text: value, start, end: start + value.length, refs: [...new Set(matched)] })
    }
  }
  return out
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function authorFamily(author: string): string {
  const value = author.replace(/\s+/g, ' ').trim()
  if (!value) return ''
  const left = value.includes(',') ? value.slice(0, value.indexOf(',')) : value
  const words = left.split(/\s+/).filter(Boolean)
  // 兼容 ``Ghafarollahi, A.``、``M. Ghafarollahi`` 和完整姓名；
  // 末尾的非首字母词就是可用于正文匹配的姓氏。
  return [...words].reverse().find((w) => !/^[A-Za-zÀ-ÿ](?:[.-])?$/.test(w)) ?? words.at(-1) ?? ''
}

function citationYear(ref: RefItem): { year: number; suffix: string } {
  const m = ref.raw.match(new RegExp(`\\b${ref.year}[a-z]?\\b`, 'i'))
  return { year: ref.year ?? 0, suffix: m?.[0].slice(String(ref.year).length).toLowerCase() ?? '' }
}

function firstAuthorFamily(authors: readonly string[]): string {
  const a = authors.find((x) => x && x !== 'et al.')
  return a ? authorFamily(a) : ''
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
