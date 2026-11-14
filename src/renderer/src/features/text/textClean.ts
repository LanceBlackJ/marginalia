/**
 * 文字阅读器的“显示层清洗”纯函数。
 * 数据库里提取出的原文一个字都不改：这里只决定“怎么显示”。规则都偏保守——拿不准就不动。
 */
import type { TextItem } from '@/lib/api.text'

/** 落单的代理项（提取时被截坏的 emoji/符号）、替换字符与控制字符：显示时去掉。 */
const BROKEN = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]|�|[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g

export function stripBroken(text: string): string {
  return text.replace(BROKEN, '')
}

// ---------------------------------------------------------------- 行末连字符

/** 构成合成词的常见前半（“well- known”“state- of- the- art”）：只去空格、保留连字符。 */
const COMPOUND_LEFT = new Set([
  'well', 'state', 'long', 'short', 'self', 'non', 'multi', 'cross', 'high', 'low', 'real', 'large', 'small', 'end', 'data', 'graph',
  'node', 'edge', 'time', 'task', 'model', 'rule', 'text', 'user', 'object', 'context', 'class', 'label', 'domain', 'feature', 'scale',
  'free', 'hand', 'full', 'least', 'first', 'second', 'third', 'one', 'two', 'three', 'four', 'many', 'single', 'mean', 'step', 'level',
  'based', 'aware', 'driven', 'wise', 'like', 'type', 'order', 'dimensional', 'layer', 'path', 'point', 'pair', 'sub', 'over', 'under'
])

/** 断词后常见的词尾片段（“connectiv- ity”“consider- ation”）：补回去直接连成一个词。 */
const SUFFIXES = new Set([
  'ity', 'ities', 'ion', 'ions', 'tion', 'tions', 'ation', 'ations', 'sion', 'ing', 'ings', 'ed', 'es', 'ly', 'ment', 'ments', 'ness',
  'ous', 'ious', 'ive', 'ives', 'al', 'ally', 'ance', 'ances', 'ence', 'ences', 'ent', 'ents', 'ant', 'ants', 'ize', 'ized', 'izes',
  'izing', 'ise', 'ised', 'able', 'ible', 'ular', 'ary', 'ory', 'ics', 'ic', 'ical', 'ically', 'er', 'ers', 'or', 'ors', 'ure', 'ures',
  'ate', 'ated', 'ates', 'ating', 'ful', 'less', 'ism', 'ist', 'ists', 'ish', 'ify', 'ified', 'tive', 'tively', 'ability', 'ibility',
  'ology', 'ological', 'graphy', 'metric', 'metrics', 'mization', 'mizing', 'mized', 'tial', 'tially', 'ular', 'ule', 'ules', 'age', 'ages',
  'ish', 'eous', 'ual', 'ually', 'sions', 'ances', 'tors', 'tor', 'ture', 'tures', 'ceed', 'ceeds', 'duced', 'duce', 'duction', 'cess'
])

/** 过短的词尾片段里，这些其实是独立的词（“node- level”“graph- to- graph”）：不合并。 */
const SHORT_WORDS = new Set(['a', 'an', 'the', 'of', 'to', 'in', 'on', 'at', 'by', 'or', 'and', 'for', 'art', 'end', 'out', 'off', 'up', 'per', 'via', 'vs', 'one', 'two'])

/**
 * 去掉行末连字符造成的断词。规则：
 * - “小写词-空格-小写词”：右半是常见词尾，或左半是 ≤3 字母的前缀（con- / in- / de-），才合并成一个词；
 * - 左半是常见合成词前半，或右半是独立短词、或两边含大写（Yang- Yu、Barabási- Albert）：只去掉空格，保留连字符；
 * - 其它拿不准的：同样只去空格，保留连字符（宁可保守）。
 */
export function dehyphenate(text: string): string {
  // 右半用前瞻捕获、不消耗：连续断开的 “state- of- the- art” 才能逐个处理
  return text.replace(/(\p{L}+)- (?=(\p{L}+))/gu, (_m, left: string, right: string) => {
    const l = left.toLowerCase()
    const r = right.toLowerCase()
    const bothLower = left === l && right === r
    if (!bothLower) return `${left}-`
    if (COMPOUND_LEFT.has(l)) return `${left}-`
    if (SHORT_WORDS.has(r)) return `${left}-`
    if (SUFFIXES.has(r)) return left
    if (l.length <= 3 && r.length >= 3) return left
    return `${left}-`
  })
}

// ---------------------------------------------------------------- 空白

/** 合并多余空格，去掉标点前、括号内侧的空格（不动数学符号两侧的空格）。 */
export function tidySpaces(text: string): string {
  return text
    .replace(/[ \t ]{2,}/g, ' ')
    .replace(/ +([,.;:!?])(?=\s|$)/g, '$1')
    .replace(/\( +/g, '(')
    .replace(/ +\)/g, ')')
    .trim()
}

/** 段落的显示文字：去坏字符、接上断词、整理空白。 */
export function cleanDisplay(text: string): string {
  return tidySpaces(dehyphenate(tidySpaces(stripBroken(text))))
}

// ---------------------------------------------------------------- 引用角标

/** 论文里出现过的“独立单词”次数，用来判断“词+数字”里的词本身是不是一个真实的词。 */
export type Vocab = Map<string, number>

export function buildVocab(items: TextItem[]): Vocab {
  const vocab: Vocab = new Map()
  for (const it of items) {
    if (it.kind !== 'paragraph' && it.kind !== 'caption') continue
    for (const m of stripBroken(it.text).matchAll(/(?<![\p{L}\d])\p{L}{3,}(?![\p{L}\d])/gu)) {
      const w = m[0]
      vocab.set(w, (vocab.get(w) ?? 0) + 1)
    }
  }
  return vocab
}

/** 数学/记号里常见的“词+数字”，不是引用。 */
const NOT_CITATION = new Set(['resnet', 'vgg', 'bert', 'gpt', 'yolo', 'densenet', 'efficientnet', 'mobilenet', 'inception', 'alexnet', 'roberta', 'llama', 'albert', 'xlnet', 'electra', 'convnext', 'unet', 'python', 'cuda', 'log', 'exp', 'top', 'sin', 'cos', 'tan', 'max', 'min', 'arg', 'dim', 'det', 'tr', 'sup', 'inf', 'lim', 'cos', 'sinh', 'tanh', 'mod'])

export interface InlinePiece {
  text: string
  sup?: boolean
}

const CITE_RE = /(?<![\p{L}\d])(\p{L}{3,})(\d{1,3}(?:[,–‐-]\d{1,3}){0,4})(?=[\s.,;:)\]—–―\-/”’。！？、；：）】》」』]|$)/gu

/**
 * 把“networks8”“information3”“systems1,2”“ANC29”里粘在词尾的引用数字拆出来，渲染成上标。
 * 只在这个词本身（去掉数字后）在论文里独立出现过时才拆；数字不能以 0 开头；数学记号（log2、top5…）不拆；拿不准就原样返回。
 */
export function splitCitations(text: string, vocab: Vocab): InlinePiece[] {
  const out: InlinePiece[] = []
  let last = 0
  for (const m of text.matchAll(CITE_RE)) {
    const word = m[1]
    const digits = m[2]
    const idx = m.index ?? 0
    const lower = word === word.toLowerCase()
    const upper = word === word.toUpperCase() && word.length >= 2
    if (!lower && !upper) continue
    if (NOT_CITATION.has(word.toLowerCase())) continue
    if (/^0\d/.test(digits)) continue
    if ((vocab.get(word) ?? 0) < (upper ? 2 : 1)) continue // 缩写要求至少独立出现两次，更保守
    if (idx > last) out.push({ text: text.slice(last, idx) })
    out.push({ text: word })
    out.push({ text: digits, sup: true })
    last = idx + m[0].length
  }
  if (last === 0) return [{ text }]
  if (last < text.length) out.push({ text: text.slice(last) })
  return out
}

// ---------------------------------------------------------------- 首句、图注

const ABBREV_END = /(?:\b(?:Fig|Figs|Eq|Eqs|al|vs|Ref|Refs|No|approx|cf|resp|etc|Dr|Prof|St)|\b[ie]\.g|\bi\.e|\b[A-Z])\.$/

/** 段落首句（用于“首句加粗”）：不在缩写（et al.、Fig.、e.g.）处断句；找不到合适的断点返回 null。 */
export function splitFirstSentence(text: string): { first: string; rest: string } | null {
  if (text.length < 140) return null
  const re = /[.!?](?=\s+[A-Z“"‘'(])/g
  for (let m = re.exec(text); m; m = re.exec(text)) {
    const end = m.index + 1
    const head = text.slice(0, end)
    if (head.length < 25) continue
    if (ABBREV_END.test(head)) continue
    if (head.length > 260) return null
    return { first: head, rest: text.slice(end) }
  }
  return null
}

export interface ParsedCaption {
  /** “图 1”“Table 1”这样的标签；识别不出为 null */
  label: string | null
  /** 图注标题（第一句，Nature 风格里“Fig. 1 | 标题. a, …”） */
  title: string
  rest: string
}

const CAPTION_RE = /^\s*((?:Extended Data |Supplementary |Supp\. )?(?:Fig(?:ure)?s?\.?|Table|Algorithm|Box)\s*[A-Z]?\d+[A-Za-z]?)\s*[|:.–—-]?\s*([\s\S]*)$/i

export function parseCaption(text: string): ParsedCaption {
  const m = CAPTION_RE.exec(text)
  if (!m) return { label: null, title: '', rest: text }
  const body = m[2]
  const label = m[1].replace(/\s+/g, ' ').replace(/^Fig(?:ure)?s?\.?\s*/i, (s) => (/^Figure/i.test(s) ? 'Figure ' : 'Fig. '))
  const end = /[.?!](?=\s|$)/.exec(body)
  if (end && end.index + 1 <= 180 && !ABBREV_END.test(body.slice(0, end.index + 1))) {
    return { label, title: body.slice(0, end.index + 1), rest: body.slice(end.index + 1).trim() }
  }
  return { label, title: '', rest: body }
}

/** 把被页边界切开的几段接成一段：上一段以连字符结尾时交给断词规则判断，否则用一个空格连接。 */
export function joinParts(texts: string[]): string {
  let out = texts[0] ?? ''
  for (const t of texts.slice(1)) out = /-$/.test(out) ? `${out} ${t}` : `${out} ${t}`
  return out
}

/** 识别出的 LaTeX 常自带 `\tag{1}`：拆出来，编号统一显示在公式右侧，避免重复。 */
export function splitTag(latex: string): { tex: string; tag: string | null } {
  const m = /\\tag\*?\{([^{}]*)\}/.exec(latex)
  if (!m) return { tex: latex, tag: null }
  return { tex: latex.replace(/\\tag\*?\{[^{}]*\}/g, '').trim(), tag: m[1].trim() || null }
}

export interface ParsedCaptionZh {
  /** “图 1”“表 2”“补充图 S3”这样的编号（没有就是 null） */
  label: string | null
  /** 图注的第一句（不太长才算标题，加粗显示） */
  title: string
  rest: string
}

/** 中文译文的图注：和原文图注一样拆成“编号 + 加粗的标题句 + 其余”。认不出编号就整段当正文。 */
export function parseCaptionZh(text: string): ParsedCaptionZh {
  const m = /^\s*((?:补充|扩展数据)?[图表][\s ]*[A-Za-z]?\d+[A-Za-z]?(?:\.\d+)?)\s*[|:：.．、\-—]?\s*/.exec(text)
  if (!m) return { label: null, title: '', rest: text }
  const body = text.slice(m[0].length)
  const end = body.search(/[。！？!?]/)
  if (end < 0 || end > 80) return { label: m[1].replace(/\s+/g, ' '), title: '', rest: body }
  return { label: m[1].replace(/\s+/g, ' '), title: body.slice(0, end + 1), rest: body.slice(end + 1).trimStart() }
}

/** 中文段落的第一句（“首句加粗”用）：第一个句末标点之前，太长或没有句末标点就不加粗。 */
export function splitFirstSentenceZh(text: string): { first: string; rest: string } | null {
  const end = text.search(/[。！？]/)
  if (end < 4 || end > 90 || end >= text.length - 1) return null
  return { first: text.slice(0, end + 1), rest: text.slice(end + 1) }
}
