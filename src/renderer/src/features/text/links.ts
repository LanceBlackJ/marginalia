/**
 * 正文里的链接识别（纯函数）：http(s) 网址、www.、doi.org、“doi:10.xxx” 与 “DOI 10.xxx”、arXiv:编号、邮箱。
 * 只在显示层使用，复制出去的仍是原文。提取出来的文字里常见这些断裂，这里保守地修复：
 *  - 点后面多了空格：`https://doi. org/10.24433/…`
 *  - 斜杠后面多了空格：`github.com/ FFrankyy/FINDER`
 *  - 末尾粘着标点或右括号：`(https://github.com/a/b).`
 * 拿不准的不拼、不链接。
 */

export type LinkKind = 'url' | 'doi' | 'arxiv' | 'email'

export interface LinkSpan {
  /** 在原文里的字符区间 [start, end)，可能包含被拆开的空格 */
  start: number
  end: number
  /** 真正要打开的地址（已去掉空格和末尾标点） */
  href: string
  kind: LinkKind
}

export type LinkPiece = { text: string } | { text: string; href: string; kind: LinkKind }

const TRAILING_PUNCT = new Set(['.', ',', ';', ':', '!', '?', "'", '"', '”', '’', '»', '*'])
const CLOSERS: Record<string, string> = { ')': '(', ']': '[', '}': '{' }

/** 链接字符：ASCII 可打印字符，去掉空白和 <>"`；遇到汉字或全角标点就停。 */
function takeToken(text: string, from: number): number {
  let i = from
  while (i < text.length) {
    const c = text.charCodeAt(i)
    if (c < 0x21 || c > 0x7e) break
    const ch = text[i]
    if (ch === '<' || ch === '>' || ch === '"' || ch === '`') break
    i++
  }
  return i
}

/** 去掉末尾的标点和不配对的右括号，返回去掉了几个字符。 */
function trimTail(s: string): { value: string; cut: number } {
  let v = s
  for (;;) {
    const last = v[v.length - 1]
    if (last === undefined) break
    if (TRAILING_PUNCT.has(last)) {
      v = v.slice(0, -1)
      continue
    }
    const open = CLOSERS[last]
    if (open) {
      const opens = v.split(open).length - 1
      const closes = v.split(last).length - 1
      if (closes > opens) {
        v = v.slice(0, -1)
        continue
      }
    }
    break
  }
  return { value: v, cut: s.length - v.length }
}

const HOST = /^https?:\/\/(?:[a-z0-9-]+\.)+[a-z0-9-]{2,}(?::\d+)?(?:[/?#]|$)/i

/** 当前网址片段以 . / - _ 结尾、下一段又像路径时，才把被空格拆开的两段拼回。 */
function joinable(tail: string, next: string): boolean {
  if (!/^[A-Za-z0-9]/.test(next)) return false
  if (/\.$/.test(tail)) return next.includes('/') // “doi.” + “org/10.2443/…”
  return next.includes('/') || /\.[A-Za-z0-9]{2,}/.test(next) // “github.com/” + “FFrankyy/FINDER”
}

function scanUrls(text: string): LinkSpan[] {
  const out: LinkSpan[] = []
  const starts = /(?:https?:\/\/|www\.|\bdoi\.org\/)/gi
  let m: RegExpExecArray | null
  let covered = 0
  while ((m = starts.exec(text))) {
    const start = m.index
    if (start < covered) continue
    // 前一个字符是字母数字时不是网址开头（如 xwww.）
    if (start > 0 && /[A-Za-z0-9]/.test(text[start - 1]) && !/^https?:/i.test(m[0])) continue
    let end = takeToken(text, start)
    for (let joins = 0; joins < 2; joins++) {
      const tail = text.slice(start, end)
      if (!/[./\-_]$/.test(tail)) break
      const sp = /^[ \t]/.exec(text.slice(end))
      if (!sp) break
      const nextEnd = takeToken(text, end + 1)
      const next = text.slice(end + 1, nextEnd)
      if (!next || !joinable(tail, next)) break
      end = nextEnd
    }
    const raw = text.slice(start, end)
    const { value, cut } = trimTail(raw.replace(/\s+/g, ''))
    const href = /^https?:/i.test(value) ? value : `https://${value}`
    if (!HOST.test(href)) {
      starts.lastIndex = start + m[0].length
      continue
    }
    const spanEnd = end - cut
    out.push({ start, end: spanEnd, href, kind: 'url' })
    covered = spanEnd
    starts.lastIndex = spanEnd
  }
  return out
}

function overlaps(spans: LinkSpan[], start: number, end: number): boolean {
  return spans.some((s) => start < s.end && end > s.start)
}

/** 找出一段文字里的所有链接（互不重叠，按位置排序）。 */
export function findLinks(text: string): LinkSpan[] {
  if (!text) return []
  const spans = scanUrls(text)

  // DOI：带 doi / DOI 前缀，或者形如 10.xxxx/… 的裸 DOI（必须有斜杠后的部分，单独的 “10.1038” 不算）
  const doiRe = /(?:\bdoi\s*:?\s*)?(?<![\w./-])(10\.\d{4,9}\/[^\s<>"\u0080-￿]+)/gi
  let m: RegExpExecArray | null
  while ((m = doiRe.exec(text))) {
    const { value } = trimTail(m[1])
    const start = m.index
    const end = start + m[0].length - (m[1].length - value.length)
    if (value.length < 9 || overlaps(spans, start, end)) continue
    spans.push({ start, end, href: `https://doi.org/${value}`, kind: 'doi' })
  }

  const arxivRe = /\barXiv:\s*((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?\/\d{7})(?:v\d+)?)/gi
  while ((m = arxivRe.exec(text))) {
    const start = m.index
    const end = start + m[0].length
    if (overlaps(spans, start, end)) continue
    spans.push({ start, end, href: `https://arxiv.org/abs/${m[1]}`, kind: 'arxiv' })
  }

  const mailRe = /(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g
  while ((m = mailRe.exec(text))) {
    const start = m.index
    const end = start + m[0].length
    if (overlaps(spans, start, end)) continue
    spans.push({ start, end, href: `mailto:${m[0]}`, kind: 'email' })
  }

  return spans.sort((a, b) => a.start - b.start)
}

/** 把文字切成“普通文字”和“链接”两种片段，按原顺序；拼起来正好是原文。 */
export function splitLinks(text: string): LinkPiece[] {
  const spans = findLinks(text)
  if (spans.length === 0) return [{ text }]
  const out: LinkPiece[] = []
  let pos = 0
  for (const s of spans) {
    if (s.start > pos) out.push({ text: text.slice(pos, s.start) })
    out.push({ text: text.slice(s.start, s.end), href: s.href, kind: s.kind })
    pos = s.end
  }
  if (pos < text.length) out.push({ text: text.slice(pos) })
  return out
}

/** 只放行 http、https 和 mailto。 */
export function isSafeHref(href: string): boolean {
  return /^(?:https?:\/\/|mailto:)/i.test(href)
}
