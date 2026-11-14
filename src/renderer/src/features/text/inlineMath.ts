/**
 * 正文里夹着的行内数学：只处理几种高置信度的写法，拿不准的一律不动（原样显示提取出的文字）。
 *  1. 单个希腊字母（σ、γ、ε…），前后不是字母：渲染成数学斜体；
 *  2. 带下划线的下标变量（v_i、x_{ij}、G_t）；
 *  3. 复杂度记号 O(…)，括号里只有变量、|·|、log 和运算符，并且至少含 | 、log 或 ^ 之一。
 * “显示原始提取文字”打开时不调用本函数。
 */

export type MathPiece = { text: string } | { tex: string; src: string }

const GREEK_COMMAND_RE = /\\(?:alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega)\s+[A-Za-z]{2,}\b/

/** 通用公式安全闸门：字体映射异常形成“希腊命令 + 裸英文单词”时不渲染伪公式。 */
export function isSafeInlineTex(tex: string): boolean {
  return !GREEK_COMMAND_RE.test(tex)
}

const GREEK: Record<string, string> = {
  α: '\\alpha', β: '\\beta', γ: '\\gamma', δ: '\\delta', ε: '\\epsilon', ϵ: '\\epsilon', ζ: '\\zeta', η: '\\eta', θ: '\\theta',
  λ: '\\lambda', μ: '\\mu', ν: '\\nu', ξ: '\\xi', π: '\\pi', ρ: '\\rho', σ: '\\sigma', τ: '\\tau', φ: '\\phi', χ: '\\chi', ψ: '\\psi', ω: '\\omega',
  Γ: '\\Gamma', Δ: '\\Delta', Θ: '\\Theta', Λ: '\\Lambda', Ξ: '\\Xi', Π: '\\Pi', Σ: '\\Sigma', Φ: '\\Phi', Ψ: '\\Psi', Ω: '\\Omega'
}

// 以“不是字母数字”开头（或开头）、以“不是字母数字”结尾（或结尾）的单个希腊字母：用前瞻/后顾，不消耗边界字符
const GREEK_RE = new RegExp(`(?<![\\p{L}\\p{N}_])([${Object.keys(GREEK).join('')}])(?![\\p{L}\\p{N}])`, 'gu')
const SUBSCRIPT_RE = /(?<![\p{L}\p{N}_\\])([A-Za-z])_(?:\{([A-Za-z0-9,]{1,4})\}|([A-Za-z0-9]{1,3}))(?![\p{L}\p{N}_])/gu
const POWER_RE = /(?<![\p{L}\p{N}_\\])((?:\d+(?:\.\d+)?|[A-Za-z]))\^(\{[A-Za-z0-9,+\-]{1,12}\}|[A-Za-z0-9]{1,12})(?![\p{L}\p{N}_])/gu
const RAW_COMMAND = String.raw`\\(?:sum|prod|int|partial|nabla|frac|sqrt|bar|hat|tilde|vec|mathbf|mathrm|mathbb|mathcal|operatorname|text|cdot|times|pm|mp|leq?|geq?|neq|approx|sim|in|notin|mid|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|nu|pi|rho|sigma|tau|phi|chi|psi|omega)`
const DELIMITED_LATEX_RE = /(?:\$([^$\n]+)\$|\\\(([\s\S]*?)\\\))/gu
// 翻译模型偶尔直接输出未包在 $...$ 中的 LaTeX。中文/标点是自然的公式边界，最多截取 180 个字符，避免吞掉整段正文。
const RAW_LATEX_RE = new RegExp(`(?<![\\p{L}\\p{N}\\\\])(${RAW_COMMAND}[^一-鿿。！？；，、\\n]{1,180}?)(?=[一-鿿。！？；，、\\n]|$)`, 'gu')
// 翻译模型还会输出没有定界符的短公式，例如 “d\\cdot s” 或 “d · s”。
// 只允许少量数学原子和运算符，并要求至少出现 LaTeX 运算符、Unicode 运算符或上下标，避免把英文连字符误判成公式。
const RAW_MATH_GROUP = String.raw`(?:\([A-Za-z0-9,+\-−–·∙⋅=<>≤≥≠≈∈∉≡ ]{1,32}\)|\[[A-Za-z0-9,+\-−–·∙⋅=<>≤≥≠≈∈∉≡ ]{1,32}\])`
const RAW_MATH_ATOM = String.raw`(?:[A-Za-z](?:_(?:\{[A-Za-z0-9,]{1,8}\}|[A-Za-z0-9]{1,4})|\^(?:\{[A-Za-z0-9,+\-]{1,12}\}|[A-Za-z0-9]{1,12}))?|\d+(?:\.\d+)?|${RAW_MATH_GROUP}|\\(?:alpha|beta|gamma|delta|epsilon|theta|lambda|mu|nu|pi|rho|sigma|tau|phi|chi|psi|omega))`
const RAW_MATH_OPERATOR = String.raw`(?:\\(?:cdot|times|pm|mp|leq?|geq?|neq|approx|sim|in|notin|mid)|[·∙⋅×±≤≥≠≈∈∉≡−–=+\-*/])`
const RAW_SYMBOL_MATH_RE = new RegExp(`(?<![A-Za-z0-9_])(${RAW_MATH_ATOM}(?:\\s*${RAW_MATH_OPERATOR}\\s*${RAW_MATH_ATOM}){1,4})(?![A-Za-z0-9_])`, 'gu')
// 论文正文常把“函数名(数学参数)”写成无定界符文本。函数名不固定，
// 因此只按结构识别：参数中必须出现逗号、下标、幂或 TeX/数学运算符，
// 避免把普通的“method(x)”误判成公式，同时覆盖 Ball、GCC、corr 等任意函数名。
const RAW_FUNCTION_MATH_RE = /(?<![\p{L}\p{N}_])([A-Za-z]{2,})\(([A-Za-z0-9_{} ,\\^+\-*/=<>≤≥≠≈∈∉]{1,80})\)(?![\p{L}\p{N}_])/gu
// O( … )：内部只允许字母数字、| + - * / ^ , . 空格和一层括号
const BIGO_RE = /(?<![\p{L}\p{N}])O\(((?:[A-Za-z0-9|+\-*/^,. ]|\([A-Za-z0-9|+\-*/^,. ]*\))+)\)/gu

/** 与后端保持一致：清掉不改变数学含义的 PDF/模型转写噪声。 */
export function normalizeInlineTex(tex: string): string {
  let t = tex.normalize('NFKC').trim()
  t = t.replace(/[\u0001\u0003]\s*([A-Za-z])/g, '\\bar{$1}')
  t = t.replace(/([A-Za-z])[¯ˉ‾]/g, '\\bar{$1}')
  t = t.replace(/'+(?=\s*})/g, '')
  t = t.replace(/\\partial\s+\\([A-Za-z]+)\s+([A-Za-z]{2,})(?=\s*[([{|])/g, (_all, command: string, tail: string) => `\\partial ${command[0].toUpperCase()}${tail}`)
  return t.replace(/\s+/g, ' ')
}

export function normalizeInlineText(text: string): string {
  let t = text.replace(/[\u0001\u0003]\s*([A-Za-z])/g, '\\bar{$1}').replace(/([A-Za-z])[¯ˉ‾]/g, '\\bar{$1}')
  t = t.replace(/'+(?=\s*})/g, '')
  return t.replace(/\\partial\s+\\([A-Za-z]+)\s+([A-Za-z]{2,})(?=\s*[([{|])/g, (_all, command: string, tail: string) => `\\partial ${command[0].toUpperCase()}${tail}`)
}

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

function normalizeRawMathTex(tex: string): string {
  return normalizeInlineTex(tex)
    .replace(/\s*[·∙⋅]\s*/g, ' \\cdot ')
    .replace(/\s*×\s*/g, ' \\times ')
    .replace(/\s*±\s*/g, ' \\pm ')
    .replace(/\s*[−–]\s*/g, ' - ')
    .replace(/\s*≤\s*/g, ' \\le ')
    .replace(/\s*≥\s*/g, ' \\ge ')
    .replace(/\s*≠\s*/g, ' \\ne ')
    .replace(/\s*≈\s*/g, ' \\approx ')
    .replace(/\s*∈\s*/g, ' \\in ')
    .replace(/\s*∉\s*/g, ' \\notin ')
    .replace(/\s*≡\s*/g, ' \\equiv ')
}

/** 把一段普通文字切成“文字 / 行内公式”交替的片段。没有可处理的写法时返回单个文字片段。 */
export function splitInlineMath(text: string): MathPiece[] {
  text = normalizeInlineText(text)
  const hits: Hit[] = []
  for (const m of text.matchAll(DELIMITED_LATEX_RE)) {
    const tex = normalizeInlineTex((m[1] ?? m[2]).trim())
    if (tex) hits.push({ start: m.index!, end: m.index! + m[0].length, tex })
  }
  for (const m of text.matchAll(RAW_LATEX_RE)) {
    const tex = normalizeInlineTex(m[1].trim())
    // 至少要有上下标、分组、运算符或第二个命令，单独一个 \alpha 不抢普通文字。
    if (!/[{}_^]|\s[+\-=*/(),]|\\(?:cdot|times|pm|mp|leq?|geq?|neq|approx|sim|in|notin|mid)|\\[A-Za-z]+.*\\[A-Za-z]+/.test(tex)) continue
    hits.push({ start: m.index!, end: m.index! + m[0].length, tex })
  }
  for (const m of text.matchAll(RAW_SYMBOL_MATH_RE)) {
    const src = m[1].trim()
    if (!/[\\·∙⋅×±≤≥≠≈∈∉≡−–]|[_^]/.test(src)) continue
    hits.push({ start: m.index!, end: m.index! + m[0].length, tex: normalizeRawMathTex(src) })
  }
  for (const m of text.matchAll(RAW_FUNCTION_MATH_RE)) {
    const args = m[2].trim()
    if (!/[,_^\\*/=<>≤≥≠≈∈∉]/.test(args)) continue
    hits.push({ start: m.index!, end: m.index! + m[0].length, tex: `\\mathrm{${m[1]}}(${args})` })
  }
  for (const m of text.matchAll(BIGO_RE)) {
    const tex = bigOTex(m[1])
    if (tex) hits.push({ start: m.index!, end: m.index! + m[0].length, tex })
  }
  for (const m of text.matchAll(SUBSCRIPT_RE)) {
    hits.push({ start: m.index!, end: m.index! + m[0].length, tex: `${m[1]}_{${m[2] ?? m[3]}}` })
  }
  for (const m of text.matchAll(POWER_RE)) {
    const exponent = m[2].startsWith('{') ? m[2] : `{${m[2]}}`
    hits.push({ start: m.index!, end: m.index! + m[0].length, tex: `${m[1]}^${exponent}` })
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
