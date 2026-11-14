import { describe, expect, it } from 'vitest'
import { isSafeInlineTex, normalizeInlineTex, normalizeInlineText, splitInlineMath } from './inlineMath'

const texOf = (s: string): string[] => splitInlineMath(s).flatMap((p) => ('tex' in p ? [p.tex] : []))
const rejoin = (s: string): string => splitInlineMath(s).map((p) => ('tex' in p ? p.src : p.text)).join('')

describe('splitInlineMath', () => {
  it('normalizes shared PDF and translation noise before rendering', () => {
    expect(normalizeInlineTex(String.raw`(d_i - 1)\sum_{j\in \partial \beta all(i,b)}`)).toBe(String.raw`(d_i - 1)\sum_{j\in \partial Ball(i,b)}`)
    expect(normalizeInlineTex(String.raw`\frac{N!}{K!(N-K)!''}`)).toBe(String.raw`\frac{N!}{K!(N-K)!}`)
    expect(normalizeInlineText('x\u0001d')).toBe(String.raw`x\bar{d}`)
    expect(normalizeInlineText('d¯i')).toBe(String.raw`\bar{d}i`)
    expect(normalizeInlineText(String.raw`\partial \beta all(i,b) \frac{N!}{K!(N-K)!''}`)).toBe(String.raw`\partial Ball(i,b) \frac{N!}{K!(N-K)!}`)
  })

  it('renders lone Greek letters', () => {
    expect(texOf('the connectivity measure σ is monotone')).toEqual(['\\sigma'])
    expect(texOf('discount factor γ and (ε) greedy')).toEqual(['\\gamma', '\\epsilon'])
  })
  it('does not touch Greek letters inside words or identifiers', () => {
    expect(texOf('the σcore value')).toEqual([])
    expect(texOf('x_σ and βeta')).toEqual([])
  })
  it('renders explicit subscripts', () => {
    expect(texOf('nodes v_i and v_{ij} in G_t')).toEqual(['v_{i}', 'v_{ij}', 'G_{t}'])
  })
  it('renders explicit powers, including a numeric power in a translated sentence', () => {
    expect(texOf('more than 10^{133} possible combinations')).toEqual(['10^{133}'])
    expect(texOf('a^2 and x_{i}')).toEqual(['a^{2}', 'x_{i}'])
    expect(texOf(String.raw`more than $10^{133}$ possible combinations`)).toEqual(['10^{133}'])
    expect(texOf(String.raw`more than \(10^{133}\) possible combinations`)).toEqual(['10^{133}'])
  })
  it('renders bare LaTeX emitted by the translation model inside Chinese text', () => {
    const parts = splitInlineMath(String.raw`集体影响力为：(d_i - 1)\sum _{j\in \partial \beta all(i,b)}(d_j - 1)) 和韧性中心性。`)
    const tex = parts.flatMap((p) => ('tex' in p ? [p.tex] : []))
    expect(tex.some((t) => t.includes(String.raw`\sum`))).toBe(true)
    expect(tex.some((t) => t.includes(String.raw`\partial`))).toBe(true)
    expect(tex.some((t) => t.includes(String.raw`\partial Ball`))).toBe(true)
  })
  it('renders short undelimited multiplication formulas emitted by translation', () => {
    expect(texOf(String.raw`节点级d\cdot s公式扩展为网络级量`)).toEqual([String.raw`d\cdot s`])
    expect(texOf('节点级d · s公式扩展为网络级量')).toEqual([String.raw`d \cdot s`])
  })
  it('keeps a function name and its mathematical arguments together', () => {
    expect(texOf('节点集合 Ball(i, l)、GCC(A_t) 以及 corr(d,s)')).toEqual(['\\mathrm{Ball}(i, l)', '\\mathrm{GCC}(A_t)', '\\mathrm{corr}(d,s)'])
    expect(texOf('ordinary method(x) text')).toEqual([])
  })
  it('renders common undelimited operator formulas from translated prose', () => {
    expect(texOf('超过 N − 1 个区间')).toEqual(['N - 1'])
    expect(texOf('其中 h = (sf)/(N−1)')).toEqual(['h = (sf)/(N - 1)'])
    expect(texOf('满足 A ∈ B 且 x ≡ y')).toEqual(['A \\in B', 'x \\equiv y'])
  })
  it('rejects a generic Greek-command plus bare-word font-mapping error', () => {
    expect(isSafeInlineTex(String.raw`(d_i - 1)\sum_{j\in \partial \beta all(i,b)}`)).toBe(false)
    expect(isSafeInlineTex(String.raw`\partial \beta x`)).toBe(true)
  })
  it('leaves snake_case identifiers and file names alone', () => {
    expect(texOf('call get_value() or open train_data.py')).toEqual([])
    expect(texOf('use_cuda flag')).toEqual([])
  })
  it('renders complexity expressions only when they look like math', () => {
    expect(texOf('time complexity O(|E|+|V|+|V|log|V|) in total')).toEqual(['O(|E|+|V|+|V|\\log |V|)'])
    expect(texOf('runs in O(n^2) time')).toEqual(['O(n^2)'])
    expect(texOf('constant O(1) memory')).toEqual([]) // 没有 | ^ log：不动
  })
  it('keeps the original text exactly when concatenated back', () => {
    for (const s of ['plain text only.', 'σ and γ, v_i, O(|E|+|V|)', 'Table S1 (ANC) 29 ε-greedy']) expect(rejoin(s)).toBe(s)
  })
  it('does not double-render a Greek letter that sits inside a complexity expression', () => {
    const t = texOf('cost O(σ^2) here')
    expect(t.length).toBeLessThanOrEqual(1)
  })
})
