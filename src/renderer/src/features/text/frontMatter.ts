/** 论文开头的“前置信息”（DOI、作者、单位、邮箱、收稿日期…）整理：显示层分类，不改数据库里的文字。 */
import type { TextItem } from '@/lib/api.text'
import { stripBroken, tidySpaces } from './textClean'

export interface FrontInfo {
  doi: { ord: number; text: string } | null
  dates: Array<{ ord: number; text: string }>
  /** 作者（已去掉单位角标与损坏符号） */
  authors: { ord: number; names: string[]; entries: AuthorEntry[] } | null
  /** 单位，一条一行；原文里“1College…. 2Department…”会被拆开 */
  affiliations: { ord: number; lines: string[] } | null
  emails: Array<{ ord: number; text: string }>
  other: Array<{ ord: number; text: string }>
  /** 被收进信息块的段落序号（正文里不再重复显示） */
  ords: Set<number>
}

export interface AuthorEntry {
  name: string
  /** 原文作者名后的单位编号、共同贡献/通讯作者符号，例如 ["*", "†", "1"]。 */
  markers: string[]
}

const FRONT_SECTIONS = new Set(['首部信息', 'front matter', 'frontmatter'])
const ABSTRACT_SECTIONS = new Set(['abstract', '摘要', 'summary'])

export function isFrontSection(title: string | null): boolean {
  return !!title && FRONT_SECTIONS.has(title.trim().toLowerCase())
}

export function isAbstractSection(title: string | null): boolean {
  return !!title && ABSTRACT_SECTIONS.has(title.trim().toLowerCase())
}

const DOI_RE = /^\s*(?:https?:\/\/(?:dx\.)?doi\.org\/\S+|doi:\s*\S+)\s*$/i
const DATE_RE = /^\s*(?:Received|Accepted|Published|Revised|Available online)\b/i
const EMAIL_RE = /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/
const AFFIL_RE = /\b(?:University|College|Institute|Department|Laborator(?:y|ies)|School|Academy|Hospital|Center|Centre|Faculty|Corporation|Inc\.|Ltd\.)\b|大学|学院|研究所|研究院|实验室|学校|医院|中心|公司|科技|部门|系/i
// 会议论文的出版信息经常和作者/单位一起落在“首部信息”里；它们不是作者，
// 即使包含很多大写词、城市和年份，也不能交给 parseAuthors 猜姓名。
const PUBLICATION_RE = /\b(?:proceedings|conference|copyright|pmlr|journal|volume|issue|published\s+by|vancouver)\b|\b(?:19|20)\d{2}\b/i

/** 作者行：去掉单位角标数字、“✉”及提取损坏的符号，用逗号/“and”拆成名字。 */
export function parseAuthors(line: string): string[] {
  const clean = stripBroken(line)
    .replace(/[✉*†‡§¶]/g, ' ')
    // 单位角标既可能独立出现，也可能紧贴姓氏；它不是作者名的一部分。
    .replace(/(?<![A-Za-z])\d{1,2}(?![A-Za-z])/g, ' ')
    .replace(/(?<=[A-Za-z])\d{1,2}(?=\s|$)/g, ' ')
    .replace(/\s+/g, ' ')
  const pieces = clean.split(/\s*(?:,|;|\band\b|&)\s*/i).map((s) => s.trim()).filter(Boolean)
  const nameToken = (s: string): boolean => /^(?:[A-ZÀ-ÖØ-Þ][\p{L}'’.-]*|[A-Z]\.)$/u.test(s)
  const out: string[] = []
  for (const piece of pieces) {
    const tokens = piece.split(/\s+/).filter(Boolean)
    // 没有逗号/and 的作者行（常见于会议论文）：连续的“名 姓 名 姓”
    // 仍然按姓名拆开；带中间缩写时保留三词姓名。
    if (pieces.length === 1 && tokens.length >= 4 && tokens.every(nameToken)) {
      for (let i = 0; i < tokens.length;) {
        const take = i + 2 < tokens.length && /^[A-Z]\.$/.test(tokens[i + 1]) ? 3 : 2
        out.push(tokens.slice(i, i + take).join(' '))
        i += take
      }
    } else {
      out.push(piece.replace(/^[\d\s,]+|[\d\s,]+$/g, '').trim())
    }
  }
  return out.filter((s) => /\p{L}{2,}/u.test(s) && s.length <= 60)
}

function authorMarkers(text: string): string[] {
  const out: string[] = []
  const tokens = text.match(/[\p{L}]+|[∗*†‡§¶♯#⋆★☯✉+]|\d{1,2}/gu) ?? []
  for (const token of tokens) {
    if (/^[\p{L}]+$/u.test(token)) break
    out.push(token)
  }
  return [...new Set(out)]
}

/** 解析作者名及其后的单位/贡献角标；姓名拆分仍复用 parseAuthors 的通用规则。 */
export function parseAuthorEntries(line: string): AuthorEntry[] {
  const names = parseAuthors(line)
  const clean = stripBroken(line)
  const out: AuthorEntry[] = []
  let cursor = 0
  for (const name of names) {
    const at = clean.toLocaleLowerCase().indexOf(name.toLocaleLowerCase(), cursor)
    if (at < 0) {
      out.push({ name, markers: [] })
      continue
    }
    const after = at + name.length
    const next = names.slice(out.length + 1).map((n) => clean.toLocaleLowerCase().indexOf(n.toLocaleLowerCase(), after)).find((p) => p >= 0)
    const gap = clean.slice(after, next ?? clean.length)
    out.push({ name, markers: authorMarkers(gap) })
    cursor = after
  }
  return out
}

/** “1College of A. 2Department of B.” → 逐条单位。 */
export function parseAffiliations(text: string): string[] {
  let clean = tidySpaces(stripBroken(text).replace(/\s+/g, ' '))
  // 首页常把贡献说明/通讯作者和第一条单位粘在同一行；这些说明不是单位，
  // 但后面的“1University…”或“1北京大学…”仍要保留。
  const firstNumbered = clean.search(/(?:^|\s)\d{1,2}(?=[A-Z\u4e00-\u9fff])/)
  if (firstNumbered > 0) {
    const prefix = clean.slice(0, firstNumbered).trim()
    if (/(?:equal contribution|contributed equally|correspondence|通讯|贡献|邮箱)/i.test(prefix) || EMAIL_RE.test(prefix)) {
      clean = clean.slice(firstNumbered).trim()
    }
  }
  const parts = clean.split(/(?:(?<=\.)\s+|\s+)(?=\d{1,2}(?=[A-Z\u4e00-\u9fff]))/).map((s) => s.trim())
  const lines = parts.filter(Boolean)
  return lines.length > 0 ? lines : [clean]
}

export interface Split {
  front: FrontInfo | null
  /** 摘要段落（按原顺序） */
  abstract: TextItem[]
  /** 摘要与信息块之外的所有段落（含它们之间的正文） */
  rest: TextItem[]
}

/**
 * 把“首部信息”章节的段落整理成信息块，把“Abstract”章节的段落单独拿出来。
 * 没有这两个章节（扫描、版式特殊）时原样返回，不做猜测。
 */
export function splitFront(items: TextItem[]): Split {
  // kind 为 front 的是解析时认出的首页作者信息（通讯作者、单位、版权许可），不管落在哪个章节都归进信息块
  const frontItems = items.filter((i) => i.kind === 'front' || (i.kind === 'paragraph' && isFrontSection(i.section_title)))
  const abstract = items.filter((i) => i.kind === 'paragraph' && isAbstractSection(i.section_title))
  let front: FrontInfo | null = null
  if (frontItems.length > 0) {
    front = { doi: null, dates: [], authors: null, affiliations: null, emails: [], other: [], ords: new Set() }
    for (const it of frontItems) {
      const text = tidySpaces(stripBroken(it.text))
      front.ords.add(it.ord)
      if (DOI_RE.test(text)) front.doi ??= { ord: it.ord, text }
      else if (DATE_RE.test(text)) front.dates.push({ ord: it.ord, text })
      else if (PUBLICATION_RE.test(text)) front.other.push({ ord: it.ord, text })
      else if (/^\d{1,2}\s*[A-Z]/.test(text) || AFFIL_RE.test(text)) {
        front.affiliations ??= { ord: it.ord, lines: [] }
        front.affiliations.lines.push(...parseAffiliations(text))
        const mails = text.match(new RegExp(EMAIL_RE, 'g'))
        if (mails) for (const e of mails) front.emails.push({ ord: it.ord, text: e })
      } else if (EMAIL_RE.test(text) && text.length < 200) front.emails.push({ ord: it.ord, text })
      else if (text.length <= 400) {
        const entries = parseAuthorEntries(it.text)
        if (entries.length > 0) {
          const current = front.authors?.entries ?? []
          const merged = [...current.map((entry) => ({ name: entry.name, markers: [...entry.markers] }))]
          for (const entry of entries) {
            const old = merged.find((item) => item.name.toLocaleLowerCase() === entry.name.toLocaleLowerCase())
            if (old) old.markers = [...new Set([...old.markers, ...entry.markers])]
            else merged.push({ name: entry.name, markers: [...entry.markers] })
          }
          front.authors = { ord: front.authors?.ord ?? it.ord, names: merged.map((entry) => entry.name), entries: merged }
        } else front.other.push({ ord: it.ord, text })
      } else front.other.push({ ord: it.ord, text })
    }
  }
  const taken = new Set<number>([...(front ? front.ords : []), ...abstract.map((a) => a.ord)])
  return { front, abstract, rest: items.filter((i) => !taken.has(i.ord)) }
}
