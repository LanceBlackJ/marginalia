/**
 * 正文里夹着的行内数学：只处理几种高置信度的写法，拿不准的一律不动（原样显示提取出的文字）。
 *  1. 单个希腊字母（σ、γ、ε…），前后不是字母：渲染成数学斜体；
 *  2. 带下划线的下标变量（v_i、x_{ij}、G_t）；
 *  3. 复杂度记号 O(…)，括号里只有变量、|·|、log 和运算符，并且至少含 | 、log 或 ^ 之一。
 * “显示原始提取文字”打开时不调用本函数。
 */

export type MathPiece = { text: string } | { tex: string; src: string }

const GREEK: Record<string, string> = {
  α: '\\alpha', β: '\\beta', γ: '\\gamma', δ: '\\delta', ε: '\\epsilon', ϵ: '\\epsilon', ζ: '\\zeta', η: '\\eta', θ: '\\theta',
  λ: '\\lambda', μ: '\\mu', ν: '\\nu', ξ: '\\xi', π: '\\pi', ρ: '\\rho', σ: '\\sigma', τ: '\\tau', φ: '\\phi', χ: '\\chi', ψ: '\\psi', ω: '\\omega',
  Γ: '\\Gamma', Δ: '\\Delta', Θ: '\\Theta', Λ: '\\Lambda', Ξ: '\\Xi', Π: '\\Pi', Σ: '\\Sigma', Φ: '\\Phi', Ψ: '\\Psi', Ω: '\\Omega'
}

// 以“不是字母数字”开头（或开头）、以“不是字母数字”结尾（或结尾）的单个希腊字母：用前瞻/后顾，不消耗边界字符
const GREEK_RE = new RegExp(`(?<![\\p{L}\\p{N}_])([${Object.keys(GREEK).join('')}])(?![\\p{L}\\p{N}])`, 'gu')
const SUBSCRIPT_RE = /(?<![\p{L}\p{N}_\\])([A-Za-z])_(?:\{([A-Za-z0-9,]{1,4})\}|([A-Za-z0-9]{1,3}))(?![\p{L}\p{N}_])/gu
// O( … )：内部只允许字母数字、| + - * / ^ , . 空格和一层括号
const BIGO_RE = /(?<![\p{L}\p{N}])O\(((?:[A-Za-z0-9|+\-*/^,. ]|\([A-Za-z0-9|+\-*/^,. ]*\))+)\)/gu

interface Hit {
  start: number
  end: number
  tex: string
}

function bigOTex(inner: string): string | null {
  if (!/[|^]|log/.test(inner)) return null // 没有 |·|、^、log：可能只是 O(1) 之类普通文字，不动
  if (inner.length > 60) return null
  const body = inner.replace(/log/g, '\\log ').replace(/\*/g, '\\cdot ').replace(/\s+/g, ' ').trim()
  return `O(${body})`
}

/** 把一段普通文字切成“文字 / 行内公式”交替的片段。没有可处理的写法时返回单个文字片段。 */
export function splitInlineMath(text: string): MathPiece[] {
  const hits: Hit[] = []
  for (const m of text.matchAll(BIGO_RE)) {
    const tex = bigOTex(m[1])
    if (tex) hits.push({ start: m.index!, end: m.index! + m[0].length, tex })
  }
  for (const m of text.matchAll(SUBSCRIPT_RE)) {
    hits.push({ start: m.index!, end: m.index! + m[0].length, tex: `${m[1]}_{${m[2] ?? m[3]}}` })
  }
  for (const m of text.matchAll(GREEK_RE)) {
    hits.push({ start: m.index!, end: m.index! + m[0].length, tex: GREEK[m[1]] })
  }
  if (hits.length === 0) return [{ text }]
  hits.sort((a, b) => a.start - b.start || b.end - a.end)
  const out: MathPiece[] = []
  let pos = 0
  for (const h of hits) {
    if (h.start < pos) continue // 与前一个命中重叠（例如 O(…) 里的希腊字母）：只保留先出现的
    if (h.start > pos) out.push({ text: text.slice(pos, h.start) })
    out.push({ tex: h.tex, src: text.slice(h.start, h.end) })
    pos = h.end
  }
  if (pos < text.length) out.push({ text: text.slice(pos) })
  return out
}
