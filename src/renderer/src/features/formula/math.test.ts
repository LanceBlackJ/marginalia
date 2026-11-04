import { describe, expect, it } from 'vitest'
import { hasMath, normalizeMath } from './math'

describe('normalizeMath', () => {
  it('converts \\( \\) and \\[ \\] to dollar delimiters', () => {
    expect(normalizeMath('令 \\( x^2 \\) 为')).toBe('令 $x^2$ 为')
    const out = normalizeMath('如下：\\[ a = b \\] 其中')
    expect(out).toContain('$$\na = b\n$$')
    expect(out.startsWith('如下：')).toBe(true)
  })

  it('does not treat dollar amounts as math', () => {
    expect(normalizeMath('It costs $5 and $10.')).toBe('It costs \\$5 and \\$10.')
    expect(normalizeMath('价格 $5')).toBe('价格 \\$5')
    expect(normalizeMath('a $ b')).toBe('a \\$ b')
  })

  it('keeps real inline math, including digit-leading and spaced forms', () => {
    expect(normalizeMath('where $5x+1$ is')).toBe('where $5x+1$ is')
    expect(normalizeMath('$ a_i $ 和 $ W_t a_i $')).toBe('$ a_i $ 和 $ W_t a_i $')
    expect(normalizeMath('$\\sigma(\\mathcal{G})$')).toBe('$\\sigma(\\mathcal{G})$')
    expect(normalizeMath('$N$')).toBe('$N$')
  })

  it('keeps display math and escaped dollars untouched', () => {
    expect(normalizeMath('$$\nx = 1\n$$')).toBe('$$\nx = 1\n$$')
    expect(normalizeMath('a \\$5 b')).toBe('a \\$5 b')
    expect(normalizeMath('$$ a $ b $$')).toBe('$$ a $ b $$')
  })

  it('leaves code untouched', () => {
    expect(normalizeMath('用 `$5` 表示')).toBe('用 `$5` 表示')
    expect(normalizeMath('```\n$x \\(y\\)\n```')).toBe('```\n$x \\(y\\)\n```')
  })

  it('wraps bare LaTeX environments as display math', () => {
    const out = normalizeMath('公式：\n\\begin{aligned}\na &= 1 \\\\\nb &= 2\n\\end{aligned}\n结束')
    expect(out).toContain('$$\n\\begin{aligned}')
    expect(out).toContain('\\end{aligned}\n$$')
  })

  it('does not run past a paragraph break looking for a closing dollar', () => {
    expect(normalizeMath('first $a\n\nsecond b$')).toBe('first \\$a\n\nsecond b\\$')
  })

  it('is a no-op for text without math', () => {
    const t = '普通文本 [P12]，没有公式。'
    expect(normalizeMath(t)).toBe(t)
    expect(normalizeMath('')).toBe('')
  })
})

describe('hasMath', () => {
  it('detects math', () => {
    expect(hasMath('x $a$ y')).toBe(true)
    expect(hasMath('\\(a\\)')).toBe(true)
    expect(hasMath('plain')).toBe(false)
  })
})
