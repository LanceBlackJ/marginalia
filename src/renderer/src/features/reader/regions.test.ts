import { describe, expect, it } from 'vitest'
import type { FigureRegion, TableAnalysisItem } from '@/lib/api.text'
import type { EquationItem } from '@/lib/types.vision'
import { buildRegions, regionAtPoint, regionForOrd, regionsOnPage } from './regions'

const eq = (id: number, page: number, bbox: [number, number, number, number], ord: number | null): EquationItem => ({ id, page, bbox, number: String(id), text: '', passage_ord: ord })
const fig = (id: number, page: number, bbox: [number, number, number, number], ord: number | null, kind: 'figure' | 'table' = 'figure'): FigureRegion => ({ id, page, bbox, kind, label: `Fig. ${id}`, caption_ord: ord })
const tbl = (ord: number, page: number, bbox: [number, number, number, number], body: number[]): TableAnalysisItem => ({
  caption_ord: ord, label: `Table ${ord}`, page, body_ords: body, bbox, status: 'none', source: null, error: null, model: null, data: null
})

describe('公式/图/表区域', () => {
  const regions = buildRegions(
    [eq(1, 2, [50, 100, 300, 140], 20)],
    new Map([[30, fig(5, 3, [40, 60, 500, 300], 30)]]),
    new Map([[40, tbl(40, 4, [40, 80, 500, 200], [41, 42, 43])]])
  )

  it('文字栏的段落对应到自己的区域框（不是段落框）', () => {
    expect(regionForOrd(regions, 20)).toMatchObject({ kind: 'equation', page: 2, bbox: [50, 100, 300, 140] })
    expect(regionForOrd(regions, 30)).toMatchObject({ kind: 'figure', page: 3 })
    expect(regionForOrd(regions, 42)).toMatchObject({ kind: 'table', page: 4 }) // 表格主体的行文字也对应到整张表
    expect(regionForOrd(regions, 999)).toBeNull()
  })

  it('PDF 上的点落在哪块区域里；重叠时取最小的', () => {
    expect(regionAtPoint(regions, 2, 100, 120)?.kind).toBe('equation')
    expect(regionAtPoint(regions, 2, 10, 10)).toBeNull()
    expect(regionAtPoint(regions, 3, 100, 100)?.kind).toBe('figure')
    const nested = buildRegions([eq(9, 3, [100, 100, 200, 130], 7)], new Map([[30, fig(5, 3, [40, 60, 500, 300], 30)]]), new Map())
    expect(regionAtPoint(nested, 3, 150, 110)?.kind).toBe('equation')
  })

  it('同一张表只保留一块区域：有表格识别项就不再重复用解析出的表区域', () => {
    const merged = buildRegions([], new Map([[40, fig(8, 4, [0, 0, 10, 10], 40, 'table')]]), new Map([[40, tbl(40, 4, [40, 80, 500, 200], [41])]]))
    expect(merged.filter((r) => r.kind === 'table')).toHaveLength(1)
    expect(merged[0].bbox).toEqual([40, 80, 500, 200])
  })

  it('按页取区域', () => {
    expect(regionsOnPage(regions, 4).map((r) => r.kind)).toEqual(['table'])
  })
})
