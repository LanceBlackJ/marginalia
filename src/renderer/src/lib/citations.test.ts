import { describe, expect, it } from 'vitest'
import { citationsToMarkdownLinks, extractOrds, parseCiteHref, parseCiteHrefRefs, parseCitations, parseRefSegments, refTag } from './citations'

describe('parseCitations', () => {
  it('解析单个引用', () => {
    expect(parseCitations('结论见 [P12]。')).toEqual([
      { type: 'text', text: '结论见 ' },
      { type: 'cite', ords: [12], raw: '[P12]' },
      { type: 'text', text: '。' }
    ])
  })

  it('相邻引用合并 [P12][P30]', () => {
    const segs = parseCitations('依据[P12][P30]成立')
    expect(segs).toHaveLength(3)
    expect(segs[1]).toMatchObject({ type: 'cite', ords: [12, 30] })
  })

  it('逗号分隔 [P12, P30] 与 [P12,P30,P41]', () => {
    expect(extractOrds('A [P12, P30] B [P1,P2,P41]')).toEqual([12, 30, 1, 2, 41])
  })

  it('支持第二个序号省略 P：[P12, 30]', () => {
    expect(extractOrds('[P12, 30]')).toEqual([12, 30])
  })

  it('被正文隔开的引用不合并', () => {
    const segs = parseCitations('[P1] 然后 [P2]')
    expect(segs.filter((s) => s.type === 'cite')).toHaveLength(2)
  })

  it('不误伤普通方括号和公式', () => {
    expect(parseCitations('x[i] = [a, b] 与 [Smith 2020]')).toEqual([{ type: 'text', text: 'x[i] = [a, b] 与 [Smith 2020]' }])
  })

  it('同一序号去重', () => {
    expect(extractOrds('[P3][P3] 又 [P3]')).toEqual([3])
  })
})

describe('citationsToMarkdownLinks / parseCiteHref', () => {
  it('转成 cite: 链接并可还原', () => {
    const md = citationsToMarkdownLinks('见 [P12][P30]。')
    expect(md).toBe('见 [P12, P30](cite:12,30)。')
    expect(parseCiteHref('cite:12,30')).toEqual([12, 30])
  })

  it('非 cite 链接返回 null', () => {
    expect(parseCiteHref('https://example.com')).toBeNull()
    expect(parseCiteHref(undefined)).toBeNull()
    expect(parseCiteHref('cite:')).toBeNull()
  })
})

describe('补充材料引用 [S1P12]', () => {
  it('解析 [S1P12] 与混写 [P3, S1P12]', () => {
    const segs = parseRefSegments('见表 [S1P12]；另见 [P3, S2P7]。')
    const cites = segs.filter((s) => s.type === 'cite')
    expect(cites).toHaveLength(2)
    expect(cites[0]).toMatchObject({ refs: [{ doc: 1, ord: 12 }] })
    expect(cites[1]).toMatchObject({ refs: [{ doc: 0, ord: 3 }, { doc: 2, ord: 7 }] })
  })

  it('相邻的正文与补充材料引用合并为一组', () => {
    const segs = parseRefSegments('依据[P3][S1P12]成立')
    expect(segs).toHaveLength(3)
    expect(segs[1]).toMatchObject({ type: 'cite', refs: [{ doc: 0, ord: 3 }, { doc: 1, ord: 12 }] })
  })

  it('同一序号在不同文档里是不同的引用（不会被去重）', () => {
    const [seg] = parseRefSegments('[P5, S1P5, S2P5]')
    expect(seg).toMatchObject({ type: 'cite' })
    expect((seg as { refs: unknown[] }).refs).toHaveLength(3)
  })

  it('裸数字沿用前一个引用的文档：[S1P12, 13]', () => {
    const [seg] = parseRefSegments('[S1P12, 13]')
    expect((seg as { refs: unknown[] }).refs).toEqual([
      { doc: 1, ord: 12 },
      { doc: 1, ord: 13 }
    ])
  })

  it('refTag 与解析互逆', () => {
    expect(refTag({ doc: 0, ord: 12 })).toBe('P12')
    expect(refTag({ doc: 2, ord: 7 })).toBe('S2P7')
  })

  it('只有正文引用时保持旧的 cite:12,30 格式', () => {
    expect(citationsToMarkdownLinks('依据 [P12, P30]')).toBe('依据 [P12, P30](cite:12,30)')
    expect(parseCiteHref('cite:12,30')).toEqual([12, 30])
  })

  it('含补充材料时使用带标记的 href，并能解析回来', () => {
    const md = citationsToMarkdownLinks('依据 [P12, S1P3]')
    expect(md).toBe('依据 [P12, S1P3](cite:P12,S1P3)')
    expect(parseCiteHrefRefs('cite:P12,S1P3')).toEqual([
      { doc: 0, ord: 12 },
      { doc: 1, ord: 3 }
    ])
    expect(parseCiteHrefRefs('cite:12,30')).toEqual([
      { doc: 0, ord: 12 },
      { doc: 0, ord: 30 }
    ])
    expect(parseCiteHrefRefs('https://example.com')).toBeNull()
  })
})
