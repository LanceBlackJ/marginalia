import { describe, expect, it } from 'vitest'
import {
  citationIndexes,
  getLastHighlightColor,
  moreKeys,
  primaryKeys,
  selectionTraits,
  setLastHighlightColor
} from './selectionToolbarModel'

const plain = { equation: false, cite: false }

describe('selectionTraits', () => {
  it('把中文为主的选区判为 chinese，英文不是', () => {
    expect(selectionTraits('这是一段关于网络瓦解的中文描述，没有英文。').chinese).toBe(true)
    expect(selectionTraits('Finding key players in complex networks').chinese).toBe(false)
    expect(selectionTraits('网络 network dismantling problem 的定义').chinese).toBe(false)
  })
  it('一个词或一个短语算 short，整句不算', () => {
    expect(selectionTraits('giant connected component').short).toBe(true)
    expect(selectionTraits('GCC').short).toBe(true)
    expect(selectionTraits('The network, or graph in discrete mathematics, is a common data structure').short).toBe(false)
    expect(selectionTraits('连通分量').short).toBe(true)
  })
})

describe('primaryKeys', () => {
  it('选的是一整段话（够长）：多一个“这段怎么读”，放在笔记前面；短选区、公式、引用不加', () => {
    expect(primaryKeys({ chinese: false, short: false }, { ...plain, long: true })).toEqual(['translate', 'explain', 'role', 'note'])
    expect(primaryKeys({ chinese: true, short: false }, { ...plain, long: true })).toEqual(['explain', 'role', 'note'])
    expect(primaryKeys({ chinese: false, short: false }, { equation: true, cite: false, long: true })).toEqual(['equation', 'note'])
  })

  it('常态主条是翻译、解释、笔记三个，翻译在第一位', () => {
    expect(primaryKeys({ chinese: false, short: false }, plain)).toEqual(['translate', 'explain', 'note'])
    expect(primaryKeys({ chinese: false, short: true }, plain)[0]).toBe('translate')
  })
  it('中文选区不出现翻译，解释放第一位', () => {
    expect(primaryKeys({ chinese: true, short: false }, plain)).toEqual(['explain', 'note'])
  })
  it('公式选区把解释换成解释公式', () => {
    const keys = primaryKeys({ chinese: false, short: false }, { equation: true, cite: false })
    expect(keys).toEqual(['equation', 'note'])
    expect(keys).not.toContain('explain')
  })
  it('引用编号选区出现查看引用，且不再给翻译', () => {
    const keys = primaryKeys({ chinese: false, short: true }, { equation: false, cite: true })
    expect(keys[0]).toBe('cite')
    expect(keys).not.toContain('translate')
  })
  it('任何情况下主条都不超过三个动作', () => {
    for (const chinese of [true, false]) {
      for (const equation of [true, false]) {
        for (const cite of [true, false]) {
          expect(primaryKeys({ chinese, short: false }, { equation, cite }).length).toBeLessThanOrEqual(3)
        }
      }
    }
  })
})

describe('moreKeys', () => {
  it('常态下更多菜单有总结、术语、设计动机、提问、高亮、复制、搜索', () => {
    const keys = moreKeys(primaryKeys({ chinese: false, short: false }, plain), { fromText: false })
    expect(keys).toEqual(['summarize', 'term', 'motivation', 'ask', 'highlight', 'copy', 'search'])
  })
  it('文字阅读器的选区没有 PDF 坐标，菜单里不放高亮', () => {
    expect(moreKeys(['translate', 'explain', 'note'], { fromText: true })).not.toContain('highlight')
  })
  it('主条没放出来的翻译和解释补进菜单，功能一个不丢', () => {
    const chinese = moreKeys(primaryKeys({ chinese: true, short: false }, plain), { fromText: false })
    expect(chinese[0]).toBe('translate')
    const equation = moreKeys(primaryKeys({ chinese: false, short: false }, { equation: true, cite: false }), { fromText: false })
    expect(equation.slice(0, 2)).toEqual(['translate', 'explain'])
  })
  it('原来工具条上的每个功能，主条加菜单里合起来都还在', () => {
    const all = new Set(['translate', 'explain', 'summarize', 'term', 'motivation', 'note', 'ask', 'highlight'])
    for (const chinese of [true, false]) {
      const p = primaryKeys({ chinese, short: false }, plain)
      const have = new Set<string>([...p.filter((k) => k !== 'equation' && k !== 'cite'), ...moreKeys(p, { fromText: false })])
      for (const k of all) expect(have.has(k)).toBe(true)
    }
  })
})

describe('citationIndexes', () => {
  it('识别纯引用编号并展开范围', () => {
    expect(citationIndexes('12')).toEqual([12])
    expect(citationIndexes('[12]')).toEqual([12])
    expect(citationIndexes('1,2')).toEqual([1, 2])
    expect(citationIndexes('[3–5]')).toEqual([3, 4, 5])
    expect(citationIndexes('1-3,7')).toEqual([1, 2, 3, 7])
  })
  it('不是引用写法就返回 null', () => {
    expect(citationIndexes('Fig. 3')).toBeNull()
    expect(citationIndexes('10.1038')).toBeNull()
    expect(citationIndexes('graph 12')).toBeNull()
    expect(citationIndexes('5-3')).toBeNull()
    expect(citationIndexes('')).toBeNull()
  })
})

describe('高亮颜色记忆', () => {
  const mem = () => {
    const m = new Map<string, string>()
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
  }
  it('没有记录时是黄色，记下后下次直接用', () => {
    const s = mem()
    expect(getLastHighlightColor(s)).toBe('yellow')
    setLastHighlightColor('blue', s)
    expect(getLastHighlightColor(s)).toBe('blue')
  })
  it('存了无效值时回到黄色，存储不可用时也不报错', () => {
    const s = mem()
    s.setItem('pd.lastHighlightColor', 'purple')
    expect(getLastHighlightColor(s)).toBe('yellow')
    expect(getLastHighlightColor(null)).toBe('yellow')
    expect(() => setLastHighlightColor('green', null)).not.toThrow()
  })
})
