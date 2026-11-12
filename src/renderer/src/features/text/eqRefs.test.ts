import { describe, expect, it } from 'vitest'
import { eqKey, findEqRefs, splitEqRefs } from './eqRefs'

const hit = (t: string): string[] => findEqRefs(t).map((r) => `${t.slice(r.start, r.end)}→${r.key}`)

describe('公式编号的规范化', () => {
  it('括号、前缀、字母后缀', () => {
    expect(eqKey('(5)')).toBe('5')
    expect(eqKey('5')).toBe('5')
    expect(eqKey('(5a)')).toBe('5a')
    expect(eqKey('Eq. (12)')).toBe('12')
    expect(eqKey(null)).toBeNull()
    expect(eqKey('abc')).toBeNull()
  })
})

describe('正文里的公式引用', () => {
  it('带前缀的写法：括号可有可无', () => {
    expect(hit('as in Eq. (3) and equation (7), see Equation 5.')).toEqual(['Eq. (3)→3', 'equation (7)→7', 'Equation 5→5'])
    expect(hit('由公式 (2) 可得')).toEqual(['公式 (2)→2'])
  })

  it('前缀后面的多个编号、范围各自成为引用', () => {
    expect(hit('Eqs. (4) and (5) hold')).toEqual(['Eqs. (4)→4', '(5)→5'])
    expect(hit('Equations 4, 5 and 6 hold')).toEqual(['Equations 4→4', '5→5', '6→6'])
    expect(hit('see Eqs. (4)–(6)')).toEqual(['Eqs. (4)→4', '(6)→6'])
  })

  it('裸的 (n)：用户截图里的那句', () => {
    const t = 'constraint (5) and(4) correspond (6) enforce boundary conditions, and equation (7) specifies the input.'
    expect(hit(t)).toEqual(['(5)→5', '(4)→4', '(6)→6', 'equation (7)→7'])
  })

  it('不当成公式引用：年份、函数调用、列举序号', () => {
    expect(hit('as shown in (2019) by f(5) and g2(3)')).toEqual([])
    expect(hit('We make three contributions: (1) we propose a model, (2) we train it.')).toEqual([])
    expect(hit('(1) We propose a model.')).toEqual([])
    expect(hit('Specifically, (3) is the key.')).toEqual([])
  })

  it('字母后缀', () => {
    expect(hit('see Eq. (5a) and (5b)')).toEqual(['Eq. (5a)→5a', '(5b)→5b'])
  })
})

describe('切片：只有论文里真有这个编号的公式才可点击', () => {
  const has = (k: string): boolean => k === '5' || k === '7'
  it('有的变成链接，没有的保持普通文字', () => {
    const t = 'constraint (5) and (4) enforce equation (7).'
    expect(splitEqRefs(t, has)).toEqual([
      { text: 'constraint ' },
      { text: '(5)', key: '5' },
      { text: ' and (4) enforce ' },
      { text: 'equation (7)', key: '7' },
      { text: '.' }
    ])
  })
  it('一个都没有时整段不动', () => {
    expect(splitEqRefs('see (9) and Eq. (3)', has)).toEqual([{ text: 'see (9) and Eq. (3)' }])
  })
  it('译文里的写法同样可点', () => {
    const t = '约束 (5) 和 (4) 分别对应，方程 (7) 指定了输入约束。'
    expect(splitEqRefs(t, has).filter((p) => 'key' in p).map((p) => p.text)).toEqual(['(5)', '(7)'])
  })
})
