import { describe, expect, it } from 'vitest'
import { splitInlineMath } from './inlineMath'

const texOf = (s: string): string[] => splitInlineMath(s).flatMap((p) => ('tex' in p ? [p.tex] : []))
const rejoin = (s: string): string => splitInlineMath(s).map((p) => ('tex' in p ? p.src : p.text)).join('')

describe('splitInlineMath', () => {
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
