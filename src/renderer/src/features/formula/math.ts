/**
 * 数学公式文本的规范化：把模型/论文里五花八门的 LaTeX 定界写法统一成 remark-math 认识的 `$…$` / `$$…$$`，
 * 同时避免把美元金额（"$5 和 $10"）误当成公式的开闭。代码块与行内代码原样保留。
 */

const CODE_RE = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g
const ENV_NAMES = 'equation\\*?|align\\*?|aligned|gather\\*?|multline\\*?|eqnarray\\*?|cases|array|matrix|pmatrix|bmatrix|vmatrix'
const BARE_ENV_RE = new RegExp(`(^|\\n)[ \\t]*(\\\\begin\\{(${ENV_NAMES})\\}[\\s\\S]*?\\\\end\\{\\3\\})[ \\t]*(?=\\n|$)`, 'g')

/** 是否为“看起来像 LaTeX”的内容（用于放宽带空格的 $ a_i $ 写法）。 */
function looksLikeMath(s: string): boolean {
  const t = s.trim()
  return (
    /[\\^_{}=<>+\-*/]|\d/.test(s) ||
    /^[A-Za-z]\w{0,2}$/.test(t) ||
    // y(t)、f(x, y)、x'、x, y 这类没有运算符的短式子也是公式（只有字母的多个单词不是）
    /^[A-Za-z][\w']*(?:\([\w\s,.'\\]*\))?'*$/.test(t) ||
    /^[A-Za-z]\w?(?:\s*,\s*[A-Za-z]\w?)+$/.test(t)
  )
}

/** 找到 from 之后能与 s[from-1] 处的 `$` 配对的闭合 `$`，找不到返回 -1。 */
function findClose(s: string, open: number): number {
  const from = open + 1
  if (from >= s.length) return -1
  const startsWithDigit = /\d/.test(s[from])
  for (let k = from; k < s.length; k++) {
    const c = s[k]
    if (c === '\\') {
      k++
      continue
    }
    if (c === '\n' && s[k + 1] === '\n') return -1 // 段落结束
    if (c !== '$') continue
    if (s[k + 1] === '$') return -1
    if (k === from) return -1 // "$$" 之类的空公式
    if (startsWithDigit) {
      // 金额判定（pandoc 规则）：以数字开头的公式，闭合 $ 前不能是空白，后面不能紧跟数字
      if (/\s/.test(s[k - 1]) || /\d/.test(s[k + 1] ?? '')) return -1
    }
    return looksLikeMath(s.slice(from, k)) ? k : -1
  }
  return -1
}

function escapeLoneDollars(s: string): string {
  let out = ''
  let i = 0
  while (i < s.length) {
    const ch = s[i]
    if (ch === '\\') {
      out += ch + (s[i + 1] ?? '')
      i += 2
      continue
    }
    if (ch === '$' && s[i + 1] === '$') {
      const j = s.indexOf('$$', i + 2)
      if (j < 0) {
        out += s.slice(i)
        break
      }
      out += s.slice(i, j + 2)
      i = j + 2
      continue
    }
    if (ch === '$') {
      const j = findClose(s, i)
      if (j > 0) {
        out += s.slice(i, j + 1)
        i = j + 1
      } else {
        out += '\\$'
        i++
      }
      continue
    }
    out += ch
    i++
  }
  return out
}

function normalizeSegment(seg: string): string {
  let s = seg
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_m, body: string) => `\n\n$$\n${body.trim()}\n$$\n\n`)
  s = s.replace(/\\\(([\s\S]+?)\\\)/g, (_m, body: string) => `$${body.trim()}$`)
  s = s.replace(BARE_ENV_RE, (_m, lead: string, env: string) => `${lead}\n$$\n${env}\n$$\n`)
  return escapeLoneDollars(s)
}

export function normalizeMath(text: string): string {
  if (!text || (!text.includes('$') && !text.includes('\\[') && !text.includes('\\(') && !text.includes('\\begin'))) return text
  return text
    .split(CODE_RE)
    .map((part, i) => (i % 2 === 1 ? part : normalizeSegment(part)))
    .join('')
}

/** 文本里是否含有会被当作公式渲染的内容（用于决定是否需要 KaTeX 兜底）。 */
export function hasMath(text: string): boolean {
  return /\$[^$\n]+\$|\$\$[\s\S]+?\$\$|\\\(|\\\[|\\begin\{/.test(text)
}
