/** 公式/图/表的区域（PDF 坐标）：文字栏与 PDF 栏互相定位时用它们自己的区域框，而不是段落框。 */
import type { BBox } from '@/lib/types'
import type { FigureRegion, TableAnalysisItem } from '@/lib/api.text'
import type { EquationItem } from '@/lib/types.vision'

export type RegionKind = 'equation' | 'figure' | 'table'

export interface Region {
  kind: RegionKind
  id: number
  page: number
  bbox: BBox
  /** 文字阅读器里对应的段落序号（公式：公式段落；图/表：图注段落） */
  ord: number | null
  /** 这块区域还对应哪些段落（表格主体的各行文字） */
  extraOrds: number[]
  label: string | null
}

export function buildRegions(
  equations: EquationItem[],
  figuresByCaption: Map<number, FigureRegion>,
  tablesByCaption: Map<number, TableAnalysisItem>
): Region[] {
  const out: Region[] = []
  for (const e of equations) {
    out.push({ kind: 'equation', id: e.id, page: e.page, bbox: e.bbox, ord: e.passage_ord, extraOrds: [], label: e.number ? `(${e.number})` : null })
  }
  const tableCaptions = new Set(tablesByCaption.keys())
  for (const f of figuresByCaption.values()) {
    // 解析阶段标成表的区域，如果已有更精确的表格识别项（来自图注推断）就用后者
    if (f.kind === 'table' && f.caption_ord !== null && tableCaptions.has(f.caption_ord)) continue
    out.push({ kind: f.kind === 'table' ? 'table' : 'figure', id: f.id, page: f.page, bbox: f.bbox, ord: f.caption_ord, extraOrds: [], label: f.label })
  }
  for (const t of tablesByCaption.values()) {
    out.push({ kind: 'table', id: t.caption_ord, page: t.page, bbox: t.bbox, ord: t.caption_ord, extraOrds: t.body_ords, label: t.label })
  }
  return out
}

/** 文字栏点了某个段落：它属于哪块公式/图/表区域（没有就返回 null，调用方退回段落框）。 */
export function regionForOrd(regions: Region[], ord: number): Region | null {
  return regions.find((r) => r.ord === ord || r.extraOrds.includes(ord)) ?? null
}

/** PDF 页面上的一点（PDF 坐标）落在哪块区域里；多块重叠时取面积最小的（最具体的）。 */
export function regionAtPoint(regions: Region[], page: number, x: number, y: number): Region | null {
  let best: Region | null = null
  let bestArea = Infinity
  for (const r of regions) {
    if (r.page !== page) continue
    const [x0, y0, x1, y1] = r.bbox
    if (x < x0 || x > x1 || y < y0 || y > y1) continue
    const area = (x1 - x0) * (y1 - y0)
    if (area < bestArea) {
      best = r
      bestArea = area
    }
  }
  return best
}

export function regionsOnPage(regions: Region[], page: number): Region[] {
  return regions.filter((r) => r.page === page)
}
