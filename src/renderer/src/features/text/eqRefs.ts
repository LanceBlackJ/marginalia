/**
 * 正文里对公式的引用（“Eq. (3)”“Equations (4)–(6)”“Equation 5”“constraint (5) and (4) correspond to …”）：识别出来，
 * 点击后打开对应的公式。只有论文里真有这个编号的公式才会变成可点击的，没有的保持普通文字。纯函数。
 *
 * 两类写法：
 * 1. 带前缀（Eq./Eqs./Equation(s)/Formula/公式）：编号可以带括号也可以不带，“and / , / – / 至”后面的其它编号也各自成为引用；
 * 2. 裸的“(n)”：n 是 1–3 位数字（不是年份）。要避开两种误判——函数调用“f(5)”，以及列举式“(1) We propose … (2) …”
 *    （位于段首或标点之后、后面直接跟单词）。
 */

export interface EqRef {
  /** 在文字里的区间 [start, end) */
  start: number
  end: number
  /** 规范化后的公式编号：“5”“5a” */
  key: string
}

/** 公式的编号（“(5)”“5”“5a”“Eq. (5)”）规范成键；认不出返回 null。 */
export function eqKey(number: string | null | undefined): string | null {
  if (!number) return null
  const m = /(\d{1,3})\s*([a-z])?\b/i.exec(number)
  return m ? `${m[1]}${(m[2] ?? '').toLowerCase()}` : null
}

const PREFIX = /(?:\b(?:Eqs?\.?|Equations?|Formulas?|Formulae)|公式)[\s ]*(?=\(?\d{1,3}[a-z]?\b)/gi
const NUM = /^\(?(\d{1,3}[a-z]?)\)?/
// “Eqs. (4) and (5)”“Equations 4, 5 and 6”“(4)–(6)”“(4) to (6)”：前缀后面跟着的其它编号
const MORE = /^[\s ]*(?:,|and|&|or|to|–|—|-|、|和|与|至)[\s ]*\(?(\d{1,3}[a-z]?)\)?/i
const BARE = /\((\d{1,3}[a-z]?)\)/g

/** 裸的“(n)”是不是列举序号：位于段首、或前面是标点，后面直接跟单词（“(1) We propose …”）。 */
function isEnumerator(text: string, start: number, end: number): boolean {
  const before = text.slice(0, start).replace(/[\s ]+$/, '')
  const afterWord = /^[\s ]+[A-Za-z]{2,}/.test(text.slice(end))
  const atBoundary = before === '' || /[.;:?!,，。；：]$/.test(before)
  return atBoundary && afterWord
}

/** 裸的“(n)”紧贴在一个很短的字母/数字记号后面（“f(5)”“g2(3)”）：是函数调用或变量，不是公式引用。 */
function isCall(text: string, start: number): boolean {
  if (start === 0 || /[\s ]/.test(text[start - 1])) return false
  const m = /([A-Za-z0-9_]+)$/.exec(text.slice(0, start))
  if (!m) return false
  const tok = m[1]
  return tok.length <= 2 || /\d/.test(tok)
}

export function findEqRefs(text: string): EqRef[] {
  const out: EqRef[] = []
  const taken: Array<[number, number]> = []
  PREFIX.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = PREFIX.exec(text))) {
    const startAt = m.index
    let pos = m.index + m[0].length
    const first = NUM.exec(text.slice(pos))
    if (!first) continue
    out.push({ start: startAt, end: pos + first[0].length, key: eqKey(first[1])! })
    pos += first[0].length
    for (;;) {
      const more = MORE.exec(text.slice(pos))
      if (!more) break
      const nStart = pos + more[0].lastIndexOf(more[1]) - (text[pos + more[0].lastIndexOf(more[1]) - 1] === '(' ? 1 : 0)
      out.push({ start: nStart, end: pos + more[0].length, key: eqKey(more[1])! })
      pos += more[0].length
    }
    taken.push([startAt, pos])
    PREFIX.lastIndex = pos
  }
  BARE.lastIndex = 0
  while ((m = BARE.exec(text))) {
    const s = m.index
    const e = s + m[0].length
    if (taken.some(([a, b]) => s >= a && s < b)) continue
    if (isEnumerator(text, s, e) || isCall(text, s)) continue
    out.push({ start: s, end: e, key: eqKey(m[1])! })
  }
  return out.sort((a, b) => a.start - b.start)
}

/** 切成“普通文字 / 可点击的公式引用”交替的片段；has 里没有的编号保持普通文字。 */
export function splitEqRefs(text: string, has: (key: string) => boolean): Array<{ text: string } | { text: string; key: string }> {
  const hits = findEqRefs(text).filter((r) => has(r.key))
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
