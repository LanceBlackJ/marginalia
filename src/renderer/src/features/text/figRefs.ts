/**
 * 正文里对图、表的引用（“Fig. 3a”“Figure 2”“Tables 1 and 2”“Extended Data Fig. 1”）：识别出来，点击后打开对应的图或表。
 * 只有论文里真的有这张图或这张表（图注被解析到）才会变成可点击的，没有的保持普通文字。纯函数。
 */

export type FigRefKind = 'fig' | 'table'

export interface FigRef {
  /** 在文字里的区间 [start, end) */
  start: number
  end: number
  kind: FigRefKind
  /** 规范化后的键，如 "fig 3"、"table 2"、"extended data fig 1" */
  key: string
}

const HEAD = /\b((?:Extended Data |Supplementary |Supp\.? )?)(Figs?\.?|Figures?|FIGS?\.?|FIGURES?|Tables?|TABLES?)\s*(?=[A-Z]?\d)/g
const HEAD_ZH = /(扩展数据|补充|附)?(图|表)[\s ]*(?=[A-Z]?\d)/g
const NUM = /^([A-Z]?\d{1,3})(?:[a-z](?:\s*[,–-]\s*[a-z])*)?/
// “Figs. 2 and 3”“Tables 1, 2”：复数后面跟着的其它编号
const MORE = /^\s*(?:,|and|&|–|-)\s*([A-Z]?\d{1,3})(?:[a-z](?:\s*[,–-]\s*[a-z])*)?/

function prefixOf(p: string): string {
  const t = p.trim().toLowerCase()
  if (t.startsWith('extended')) return 'extended data '
  if (t.startsWith('supp')) return 'supplementary '
  return ''
}

/** 图注里的标签（“Fig. 2”“Figure 2”“Table 3”“Extended Data Fig. 1”）规范成同样的键；不是图表标签返回 null。 */
export function labelKey(label: string | null | undefined): string | null {
  if (!label) return null
  const m = /^\s*((?:Extended Data |Supplementary |Supp\.? )?)(Figs?\.?|Figures?|Tables?)\s*([A-Z]?\d{1,3})/i.exec(label)
  if (!m) return null
  const kind = /^t/i.test(m[2]) ? 'table' : 'fig'
  return `${prefixOf(m[1])}${kind} ${m[3].toUpperCase()}`
}

export function findFigRefs(text: string): FigRef[] {
  const out: FigRef[] = []
  HEAD.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = HEAD.exec(text))) {
    const kind: FigRefKind = /^t/i.test(m[2]) ? 'table' : 'fig'
    const plural = /s\.?$/i.test(m[2]) && !/^figures?$/i.test(m[2]) ? true : /^(figs|tables|figures)/i.test(m[2])
    let pos = m.index + m[0].length
    const first = NUM.exec(text.slice(pos))
    if (!first) continue
    out.push({ start: m.index, end: pos + first[0].length, kind, key: `${prefixOf(m[1])}${kind} ${first[1].toUpperCase()}` })
    pos += first[0].length
    if (plural) {
      for (;;) {
        const more = MORE.exec(text.slice(pos))
        if (!more) break
        const nStart = pos + more[0].indexOf(more[1])
        out.push({ start: nStart, end: pos + more[0].length, kind, key: `${prefixOf(m[1])}${kind} ${more[1].toUpperCase()}` })
        pos += more[0].length
      }
    }
    HEAD.lastIndex = pos
  }
  // 译文里的中文写法：“图 2”“图2a”“表 1”“补充图 S1”“扩展数据图 3”；“图 2 和图 3”里每个“图”各自成一个引用
  HEAD_ZH.lastIndex = 0
  while ((m = HEAD_ZH.exec(text))) {
    const kind: FigRefKind = m[2] === '表' ? 'table' : 'fig'
    const prefix = m[1] === '扩展数据' ? 'extended data ' : m[1] === '补充' || m[1] === '附' ? 'supplementary ' : ''
    const pos = m.index + m[0].length
    const first = NUM.exec(text.slice(pos))
    if (!first) continue
    out.push({ start: m.index, end: pos + first[0].length, kind, key: `${prefix}${kind} ${first[1].toUpperCase()}` })
    HEAD_ZH.lastIndex = pos + first[0].length
  }
  return out.sort((a, b) => a.start - b.start)
}

/** 切成“普通文字 / 可点击的图表引用”交替的片段；refs 里没有的键保持普通文字。 */
export function splitFigRefs(text: string, has: (key: string) => boolean): Array<{ text: string } | { text: string; key: string }> {
  const hits = findFigRefs(text).filter((r) => has(r.key))
  if (hits.length === 0) return [{ text }]
  const out: Array<{ text: string } | { text: string; key: string }> = []
  let pos = 0
  for (const h of hits) {
    if (h.start < pos) continue
    if (h.start > pos) out.push({ text: text.slice(pos, h.start) })
    out.push({ text: text.slice(h.start, h.end), key: h.key })
    pos = h.end
  }
  if (pos < text.length) out.push({ text: text.slice(pos) })
  return out
}
