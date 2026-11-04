import { describe, expect, it } from 'vitest'
import { findFigRefs, labelKey, splitFigRefs } from './figRefs'

describe('图表标签的规范化', () => {
  it('不同写法归到同一个键', () => {
    expect(labelKey('Fig. 2')).toBe('fig 2')
    expect(labelKey('Figure 2')).toBe('fig 2')
    expect(labelKey('FIGURE 2')).toBe('fig 2')
    expect(labelKey('Table 3')).toBe('table 3')
    expect(labelKey('Extended Data Fig. 1')).toBe('extended data fig 1')
    expect(labelKey('Supplementary Table 1')).toBe('supplementary table 1')
    expect(labelKey('图 2')).toBeNull()
    expect(labelKey(null)).toBeNull()
  })
})

describe('正文里的图表引用', () => {
  it('识别 Fig.、Figure、Table，子图字母并入同一张图', () => {
    const t = 'As shown in Fig. 3a and Figure 2, see Table 4 for details.'
    expect(findFigRefs(t).map((r) => [t.slice(r.start, r.end), r.key])).toEqual([
      ['Fig. 3a', 'fig 3'],
      ['Figure 2', 'fig 2'],
      ['Table 4', 'table 4']
    ])
  })

  it('复数后面的其它编号也各自成为引用', () => {
    const t = 'Figs. 2 and 3 and Tables 1, 2 show it.'
    expect(findFigRefs(t).map((r) => r.key)).toEqual(['fig 2', 'fig 3', 'table 1', 'table 2'])
  })

  it('带 Extended Data、Supplementary 前缀的单独成键，不和正文图混淆', () => {
    const t = 'See Extended Data Fig. 1 and Supplementary Table 2.'
    expect(findFigRefs(t).map((r) => r.key)).toEqual(['extended data fig 1', 'supplementary table 2'])
  })

  it('没有编号的“the figure”不算', () => {
    expect(findFigRefs('the figure below and a table of results')).toEqual([])
  })

  it('只有论文里存在的图表才变成可点击的片段，文字总和不变', () => {
    const t = 'In Fig. 5 and Fig. 9 we show'
    const out = splitFigRefs(t, (k) => k === 'fig 5')
    expect(out.map((p) => p.text).join('')).toBe(t)
    expect(out.filter((p) => 'key' in p)).toEqual([{ text: 'Fig. 5', key: 'fig 5' }])
  })
})
