import { describe, expect, it } from 'vitest'
import {
  PADDING,
  PAGE_GAP,
  clampScale,
  computeLayout,
  computeScale,
  currentPageAt,
  groupPages,
  nextZoomStep,
  rowAtOffset,
  visibleRowRange,
  type PageSize
} from './layout'

const A4: PageSize = { width: 600, height: 800 }
const pages = (n: number): PageSize[] => Array.from({ length: n }, () => A4)

describe('groupPages', () => {
  it('连续/单页每行 1 页，双页每行 2 页', () => {
    expect(groupPages(3, 'continuous')).toEqual([[1], [2], [3]])
    expect(groupPages(5, 'double')).toEqual([[1, 2], [3, 4], [5]])
    expect(groupPages(0, 'single')).toEqual([])
  })
})

describe('computeLayout', () => {
  it('行的 top 依次累加页间距', () => {
    const l = computeLayout(pages(3), 'continuous', 1)
    expect(l.rows[0].top).toBe(PADDING)
    expect(l.rows[1].top).toBe(PADDING + 800 + PAGE_GAP)
    expect(l.totalHeight).toBe(PADDING + 3 * 800 + 2 * PAGE_GAP + PADDING)
    expect(l.pageToRow).toEqual([0, 1, 2])
  })

  it('缩放影响行高与宽', () => {
    const l = computeLayout(pages(2), 'continuous', 0.5)
    expect(l.rows[0].height).toBe(400)
    expect(l.maxWidth).toBe(300)
  })

  it('双页：行宽为两页之和加间距，页偏移正确', () => {
    const l = computeLayout(pages(4), 'double', 1)
    expect(l.rows).toHaveLength(2)
    expect(l.rows[0].width).toBe(600 * 2 + 8)
    expect(l.rows[0].offsets).toEqual([0, 608])
    expect(l.pageToRow).toEqual([0, 0, 1, 1])
  })

  it('页面尺寸不一时取行内最高', () => {
    const l = computeLayout([{ width: 600, height: 800 }, { width: 600, height: 900 }], 'double', 1)
    expect(l.rows[0].height).toBe(900)
  })
})

describe('可视范围', () => {
  const l = computeLayout(pages(20), 'continuous', 1)

  it('rowAtOffset 二分命中', () => {
    expect(rowAtOffset(l, 0)).toBe(0)
    expect(rowAtOffset(l, l.rows[7].top)).toBe(7)
    expect(rowAtOffset(l, l.rows[7].top + 10)).toBe(7)
    expect(rowAtOffset(l, 1e9)).toBe(19)
  })

  it('visibleRowRange 含 overscan 且不越界', () => {
    const [a, b] = visibleRowRange(l, 0, 900, 1)
    expect(a).toBe(0)
    expect(b).toBeGreaterThanOrEqual(2)
    const [c, d] = visibleRowRange(l, l.totalHeight, 900, 2)
    expect(d).toBe(19)
    expect(c).toBeLessThanOrEqual(19)
  })

  it('currentPageAt 取视口上 1/4 处所在行', () => {
    expect(currentPageAt(l, 0, 800)).toBe(1)
    expect(currentPageAt(l, l.rows[4].top, 800)).toBe(5)
  })

  it('空文档不崩溃', () => {
    const empty = computeLayout([], 'continuous', 1)
    expect(visibleRowRange(empty, 0, 800)).toEqual([0, -1])
    expect(currentPageAt(empty, 0, 800)).toBe(1)
  })
})

describe('computeScale', () => {
  const base = { sizes: pages(3), mode: 'continuous' as const, containerWidth: 832, containerHeight: 900 }

  it('custom 直接使用并限制范围', () => {
    expect(computeScale({ ...base, zoomMode: 'custom', customScale: 1.5 })).toBe(1.5)
    expect(computeScale({ ...base, zoomMode: 'custom', customScale: 99 })).toBe(5)
    expect(clampScale(0.01)).toBe(0.25)
  })

  it('适合宽度：扣除左右留白', () => {
    expect(computeScale({ ...base, zoomMode: 'fit-width', customScale: 1 })).toBeCloseTo((832 - 2 * PADDING) / 600)
  })

  it('适合页面：取宽高约束中较小者', () => {
    const s = computeScale({ ...base, zoomMode: 'fit-page', customScale: 1 })
    expect(s).toBeCloseTo((900 - 2 * PADDING) / 800)
  })

  it('容器尺寸未知时回退到自定义比例', () => {
    expect(computeScale({ ...base, containerWidth: 0, zoomMode: 'fit-width', customScale: 1.25 })).toBe(1.25)
  })
})

describe('nextZoomStep', () => {
  it('在档位间步进', () => {
    expect(nextZoomStep(1, 1)).toBe(1.25)
    expect(nextZoomStep(1, -1)).toBe(0.75)
    expect(nextZoomStep(1.1, 1)).toBe(1.25)
    expect(nextZoomStep(1.1, -1)).toBe(1)
    expect(nextZoomStep(4, 1)).toBe(5)
    expect(nextZoomStep(0.5, -1)).toBe(0.25)
  })
})

describe('裁掉白边的适应宽度', () => {
  const sizes = [{ width: 595, height: 842 }]
  const base = { zoomMode: 'fit-width' as const, customScale: 1, sizes, mode: 'continuous' as const, containerWidth: 700, containerHeight: 800 }

  it('按内容宽度（而不是整页宽度）适应，缩放比更大', () => {
    const full = computeScale({ ...base })
    const cropped = computeScale({ ...base, crop: { left: 40, right: 560 } })
    expect(full).toBeCloseTo((700 - 2 * PADDING) / 595, 3)
    expect(cropped).toBeCloseTo((700 - 2 * PADDING) / 520, 3)
    expect(cropped).toBeGreaterThan(full)
  })

  it('只在适应宽度的单页排版里生效', () => {
    const crop = { left: 40, right: 560 }
    expect(computeScale({ ...base, zoomMode: 'custom', customScale: 1.5, crop })).toBe(1.5)
    expect(computeScale({ ...base, zoomMode: 'fit-page', crop })).toBeCloseTo(computeScale({ ...base, zoomMode: 'fit-page' }), 5)
    expect(computeScale({ ...base, mode: 'double', crop })).toBeCloseTo(computeScale({ ...base, mode: 'double' }), 5)
  })
})
