/**
 * PDF 阅读器的纯布局计算（不依赖 DOM / pdf.js），便于单元测试。
 * 页面按“行”组织：连续/单页模式每行 1 页，双页模式每行 2 页（跨页）。
 */

export type ViewMode = 'continuous' | 'single' | 'double'

export interface PageSize {
  /** 缩放为 1 时的宽高（PDF point） */
  width: number
  height: number
}

export interface Row {
  index: number
  /** 1 起始的页码 */
  pages: number[]
  top: number
  height: number
  width: number
  /** 每页在行内的 x 偏移 */
  offsets: number[]
}

export interface Layout {
  rows: Row[]
  totalHeight: number
  maxWidth: number
  /** pageToRow[page-1] = 行号 */
  pageToRow: number[]
}

export const PAGE_GAP = 12
export const SPREAD_GAP = 8
export const PADDING = 16
export const MIN_SCALE = 0.25
export const MAX_SCALE = 5
export const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4]

export function groupPages(pageCount: number, mode: ViewMode): number[][] {
  const rows: number[][] = []
  const per = mode === 'double' ? 2 : 1
  for (let p = 1; p <= pageCount; p += per) {
    const row: number[] = []
    for (let i = 0; i < per && p + i <= pageCount; i++) row.push(p + i)
    rows.push(row)
  }
  return rows
}

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale))
}

export function computeLayout(sizes: PageSize[], mode: ViewMode, scale: number): Layout {
  const groups = groupPages(sizes.length, mode)
  const rows: Row[] = []
  const pageToRow: number[] = new Array(sizes.length).fill(0)
  let top = PADDING
  let maxWidth = 0
  groups.forEach((pages, index) => {
    const offsets: number[] = []
    let x = 0
    let height = 0
    for (const p of pages) {
      const s = sizes[p - 1]
      offsets.push(x)
      x += s.width * scale + SPREAD_GAP
      height = Math.max(height, s.height * scale)
      pageToRow[p - 1] = index
    }
    const width = Math.max(0, x - SPREAD_GAP)
    rows.push({ index, pages, top, height, width, offsets })
    maxWidth = Math.max(maxWidth, width)
    top += height + PAGE_GAP
  })
  return { rows, totalHeight: Math.max(0, top - PAGE_GAP) + PADDING, maxWidth, pageToRow }
}

/** 二分查找：y 所在（或其上方最近）的行。 */
export function rowAtOffset(layout: Layout, y: number): number {
  const { rows } = layout
  if (rows.length === 0) return 0
  let lo = 0
  let hi = rows.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (rows[mid].top <= y) lo = mid
    else hi = mid - 1
  }
  return lo
}

/** 可视范围内（含 overscan）需要渲染的行区间 [first, last]。 */
export function visibleRowRange(layout: Layout, scrollTop: number, viewportHeight: number, overscan = 1): [number, number] {
  if (layout.rows.length === 0) return [0, -1]
  const first = rowAtOffset(layout, scrollTop)
  let last = rowAtOffset(layout, scrollTop + viewportHeight)
  if (last < first) last = first
  return [Math.max(0, first - overscan), Math.min(layout.rows.length - 1, last + overscan)]
}

/** 当前页：视口上 1/4 处所在的行的第一页。 */
export function currentPageAt(layout: Layout, scrollTop: number, viewportHeight: number): number {
  if (layout.rows.length === 0) return 1
  const row = layout.rows[rowAtOffset(layout, scrollTop + viewportHeight * 0.25)]
  return row.pages[0]
}

export type ZoomMode = 'custom' | 'fit-width' | 'fit-page'

/** 计算有效缩放比例。sizes 为空时返回 1。 */
export function computeScale(opts: {
  zoomMode: ZoomMode
  customScale: number
  sizes: PageSize[]
  mode: ViewMode
  containerWidth: number
  containerHeight: number
  /** 裁掉左右白边：适应宽度时按这段内容宽度算（只对单页排版生效） */
  crop?: { left: number; right: number } | null
}): number {
  const { zoomMode, customScale, sizes, mode, containerWidth, containerHeight, crop } = opts
  if (zoomMode === 'custom' || sizes.length === 0 || containerWidth <= 0) return clampScale(customScale)
  const groups = groupPages(sizes.length, mode)
  let fitW = Infinity
  let fitH = Infinity
  for (const g of groups) {
    const sumW = g.reduce((acc, p) => acc + sizes[p - 1].width, 0)
    const gaps = (g.length - 1) * SPREAD_GAP
    const h = Math.max(...g.map((p) => sizes[p - 1].height))
    fitW = Math.min(fitW, (containerWidth - 2 * PADDING - gaps) / sumW)
    if (containerHeight > 0) fitH = Math.min(fitH, (containerHeight - 2 * PADDING) / h)
  }
  if (crop && zoomMode === 'fit-width' && mode !== 'double' && crop.right > crop.left) {
    return clampScale((containerWidth - 2 * PADDING) / (crop.right - crop.left))
  }
  if (zoomMode === 'fit-width' || !Number.isFinite(fitH)) return clampScale(fitW)
  return clampScale(Math.min(fitW, fitH))
}

export function nextZoomStep(current: number, direction: 1 | -1): number {
  const eps = 0.005
  if (direction === 1) {
    const next = ZOOM_STEPS.find((s) => s > current + eps)
    return next ?? MAX_SCALE
  }
  const lower = [...ZOOM_STEPS].reverse().find((s) => s < current - eps)
  return lower ?? MIN_SCALE
}
