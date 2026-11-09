import type { Work } from '@/lib/types.discovery'

/** 检索结果的导出：BibTeX、RIS（导入 Zotero / EndNote / Word）、CSV（Excel 能直接打开）、按格式生成的参考文献文本。纯函数，不联网。 */

const CJK = /[\u3400-\u9fff]/

interface Name {
  family: string
  given: string
  cjk: boolean
}

export function splitName(full: string): Name {
  const s = full.trim().replace(/\s+/g, ' ')
  if (!s) return { family: '', given: '', cjk: false }
  if (CJK.test(s)) return { family: s, given: '', cjk: true }
  if (s.includes(',')) {
    const [family, given] = s.split(',').map((x) => x.trim())
    return { family, given: given ?? '', cjk: false }
  }
  const parts = s.split(' ')
  if (parts.length === 1) return { family: parts[0], given: '', cjk: false }
  return { family: parts[parts.length - 1], given: parts.slice(0, -1).join(' '), cjk: false }
}

const initials = (given: string, dots = true): string =>
  given
    .split(/[\s-]+/)
    .filter(Boolean)
    .map((p) => p[0].toUpperCase() + (dots ? '.' : ''))
    .join(dots ? ' ' : '')

const doiOf = (w: Work): string => (w.doi ?? '').replace(/^https?:\/\/(dx\.)?doi\.org\//i, '')

function bibKey(w: Work, used: Set<string>): string {
  const fam = splitName(w.authors[0] ?? 'anon').family.normalize('NFKD').replace(/[^A-Za-z0-9]/g, '').toLowerCase() || 'anon'
  const word = (w.title.match(/[A-Za-z0-9]{4,}/) ?? ['work'])[0].toLowerCase()
  let key = `${fam}${w.year ?? ''}${word}`
  for (let i = 2; used.has(key); i++) key = `${fam}${w.year ?? ''}${word}${i}`
  used.add(key)
  return key
}

const bibEscape = (s: string): string => s.replace(/[{}\\]/g, (c) => `\\${c}`).replace(/&/g, '\\&').replace(/%/g, '\\%').replace(/#/g, '\\#')

export function toBibtex(works: Work[]): string {
  const used = new Set<string>()
  return (
    works
      .map((w) => {
        const conf = w.venue_type === 'conference'
        const type = conf ? 'inproceedings' : w.venue_type === 'repository' || w.status === 'preprint' ? 'misc' : 'article'
        const fields: Array<[string, string | null | undefined]> = [
          ['title', `{${bibEscape(w.title)}}`],
          ['author', w.authors.length ? w.authors.map((a) => { const n = splitName(a); return n.given ? `${n.family}, ${n.given}` : n.family }).join(' and ') : null],
          ['year', w.year ? String(w.year) : null],
          [conf ? 'booktitle' : 'journal', w.venue && type !== 'misc' ? bibEscape(w.venue) : null],
          ['howpublished', type === 'misc' && w.venue ? bibEscape(w.venue) : null],
          ['doi', doiOf(w) || null],
          ['eprint', w.arxiv_id],
          ['url', w.url],
          ['abstract', w.abstract ? bibEscape(w.abstract) : null]
        ]
        const body = fields.filter(([, v]) => v).map(([k, v]) => `  ${k} = {${(v as string).replace(/^\{(.*)\}$/s, '$1')}}`)
        return `@${type}{${bibKey(w, used)},\n${body.join(',\n')}\n}`
      })
      .join('\n\n') + '\n'
  )
}

export function toRis(works: Work[]): string {
  return works
    .map((w) => {
      const ty = w.venue_type === 'conference' ? 'CONF' : w.status === 'preprint' ? 'UNPB' : w.venue_type === 'book series' ? 'CHAP' : 'JOUR'
      const lines: string[] = [`TY  - ${ty}`, `TI  - ${w.title}`]
      for (const a of w.authors) lines.push(`AU  - ${a}`)
      if (w.year) lines.push(`PY  - ${w.year}`)
      if (w.venue) lines.push(`${ty === 'CONF' ? 'T2' : 'JO'}  - ${w.venue}`)
      if (doiOf(w)) lines.push(`DO  - ${doiOf(w)}`)
      if (w.url) lines.push(`UR  - ${w.url}`)
      if (w.abstract) lines.push(`AB  - ${w.abstract.replace(/\s+/g, ' ')}`)
      lines.push('ER  - ')
      return lines.join('\r\n')
    })
    .join('\r\n') + '\r\n'
}

const csvCell = (v: unknown): string => {
  const s = v == null ? '' : String(v)
  // 以 = + - @ 开头的单元格会被 Excel 当公式执行：前面加一个单引号
  const safe = /^[=+\-@]/.test(s) ? `'${s}` : s
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe
}

export const CSV_COLUMNS: Array<[string, (w: Work) => unknown]> = [
  ['标题', (w) => w.title],
  ['作者', (w) => w.authors.join('; ')],
  ['年份', (w) => w.year],
  ['期刊/会议', (w) => w.venue],
  ['类型', (w) => w.kind_label],
  ['发表状态', (w) => w.status_label],
  ['被引次数', (w) => w.citations],
  ['DOI', (w) => doiOf(w)],
  ['arXiv', (w) => w.arxiv_id],
  ['链接', (w) => w.url],
  ['开放获取', (w) => (w.open_access === null ? '' : w.open_access ? '是' : '否')],
  ['领域', (w) => w.field],
  ['摘要', (w) => w.abstract]
]

/** 带 BOM 的 CSV：Excel 直接双击打开中文不乱码。 */
export function toCsv(works: Work[]): string {
  const head = CSV_COLUMNS.map(([h]) => csvCell(h)).join(',')
  const rows = works.map((w) => CSV_COLUMNS.map(([, f]) => csvCell(f(w))).join(','))
  return '\uFEFF' + [head, ...rows].join('\r\n') + '\r\n'
}

export type RefStyle = 'gbt7714' | 'apa' | 'ieee'
export const REF_STYLES: Array<{ value: RefStyle; label: string }> = [
  { value: 'gbt7714', label: 'GB/T 7714' },
  { value: 'apa', label: 'APA' },
  { value: 'ieee', label: 'IEEE' }
]

function gbtType(w: Work): string {
  if (w.venue_type === 'conference') return 'C'
  if (w.status === 'preprint' || w.venue_type === 'repository') return 'EB/OL'
  if (w.venue_type === 'book series') return 'M'
  return 'J'
}

/** 一条参考文献文本。信息缺失时直接省略那一段，不编造。 */
export function formatReference(w: Work, style: RefStyle): string {
  const names = w.authors.map(splitName)
  const doi = doiOf(w)
  if (style === 'gbt7714') {
    const au = names.length === 0 ? '' : (names.slice(0, 3).map((n) => (n.cjk ? n.family : `${n.family.toUpperCase()} ${initials(n.given, false)}`.trim())).join(', ') + (names.length > 3 ? ', 等' : ''))
    const parts = [au ? `${au}. ` : '', `${w.title}[${gbtType(w)}]. `, w.venue ? `${w.venue}` : '', w.year ? `${w.venue ? ', ' : ''}${w.year}` : '', '.', doi ? ` DOI:${doi}.` : '']
    return parts.join('').replace(/\s+\./g, '.').replace(/\.\./g, '.')
  }
  if (style === 'apa') {
    const fmt = names.map((n) => (n.cjk ? n.family : `${n.family}, ${initials(n.given)}`.replace(/, $/, '')))
    const au = fmt.length <= 1 ? (fmt[0] ?? '') : fmt.length <= 20 ? `${fmt.slice(0, -1).join(', ')}, & ${fmt[fmt.length - 1]}` : `${fmt.slice(0, 19).join(', ')}, … ${fmt[fmt.length - 1]}`
    return [au ? `${au} ` : '', `(${w.year ?? 'n.d.'}). `, `${w.title.replace(/\.$/, '')}. `, w.venue ? `${w.venue}. ` : '', doi ? `https://doi.org/${doi}` : (w.url ?? '')].join('').trim()
  }
  const au = names.map((n) => (n.cjk ? n.family : `${initials(n.given)} ${n.family}`.trim()))
  const auText = au.length <= 2 ? au.join(' and ') : `${au.slice(0, -1).join(', ')}, and ${au[au.length - 1]}`
  return [auText ? `${auText}, ` : '', `"${w.title}," `, w.venue ? `${w.venue}, ` : '', w.year ? `${w.year}` : '', doi ? `, doi: ${doi}.` : '.'].join('')
}

export function toReferenceList(works: Work[], style: RefStyle): string {
  return works.map((w, i) => (style === 'apa' ? formatReference(w, style) : `[${i + 1}] ${formatReference(w, style)}`)).join('\n') + '\n'
}
