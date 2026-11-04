import type { BBox } from '@/lib/types'

export interface ClientRectLike {
  left: number
  top: number
  right: number
  bottom: number
}

export interface SelectionInfo {
  text: string
  page: number
  /** PDF point 坐标（左上角原点），按行合并 */
  rects: BBox[]
  /** 视口坐标，用于放置浮动工具栏 */
  clientRect: ClientRectLike
  /** 选区起点所在段落（文字阅读器里的选区才有） */
  ord?: number
  /** 选区来自文字阅读器：没有 PDF 坐标，不能做 PDF 高亮 */
  fromText?: boolean
}

function pageElementOf(node: Node | null): HTMLElement | null {
  const el = node && node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement | null)
  return el?.closest<HTMLElement>('.pd-page-view') ?? null
}

export function normalizeSelectionText(raw: string): string {
  return raw
    .replace(/­/g, '')
    .replace(/(\w)-\s*\n\s*(?=[a-z])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

/** 把同一行上相邻的小矩形合并，减少批注矩形数量。 */
export function mergeLineRects(rects: BBox[]): BBox[] {
  const sorted = [...rects].sort((a, b) => a[1] - b[1] || a[0] - b[0])
  const out: BBox[] = []
  for (const r of sorted) {
    const prev = out[out.length - 1]
    if (prev) {
      const h = Math.min(prev[3] - prev[1], r[3] - r[1])
      const overlap = Math.min(prev[3], r[3]) - Math.max(prev[1], r[1])
      const gap = r[0] - prev[2]
      if (overlap > h * 0.5 && gap < 6) {
        prev[0] = Math.min(prev[0], r[0])
        prev[1] = Math.min(prev[1], r[1])
        prev[2] = Math.max(prev[2], r[2])
        prev[3] = Math.max(prev[3], r[3])
        continue
      }
    }
    out.push([...r] as BBox)
  }
  return out
}

/** 读取当前文字选择：只认阅读器文字层内的选择；页码取选区起点所在页。 */
export function readSelection(root: HTMLElement): SelectionInfo | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
  const range = sel.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return null
  const pageEl = pageElementOf(range.startContainer)
  if (!pageEl) return null
  const text = normalizeSelectionText(sel.toString())
  if (!text) return null

  const page = Number(pageEl.dataset.page)
  const scale = Number(pageEl.dataset.scale) || 1
  const box = pageEl.getBoundingClientRect()
  const rects: BBox[] = []
  for (const r of Array.from(range.getClientRects())) {
    if (r.width < 1 || r.height < 1) continue
    // 只保留落在该页内的部分
    if (r.bottom < box.top || r.top > box.bottom || r.right < box.left || r.left > box.right) continue
    const x0 = Math.max(0, (r.left - box.left) / scale)
    const y0 = Math.max(0, (r.top - box.top) / scale)
    const x1 = Math.min(box.width / scale, (r.right - box.left) / scale)
    const y1 = Math.min(box.height / scale, (r.bottom - box.top) / scale)
    if (x1 > x0 && y1 > y0) rects.push([x0, y0, x1, y1])
  }
  if (rects.length === 0) return null

  const bounds = range.getBoundingClientRect()
  return {
    text,
    page,
    rects: mergeLineRects(rects),
    clientRect: { left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom }
  }
}

/** 把页内点击位置转换为 PDF point 坐标。 */
export function pointInPage(pageEl: HTMLElement, clientX: number, clientY: number): { x: number; y: number } {
  const scale = Number(pageEl.dataset.scale) || 1
  const box = pageEl.getBoundingClientRect()
  return { x: (clientX - box.left) / scale, y: (clientY - box.top) / scale }
}

export function pageElementFromTarget(target: EventTarget | null): HTMLElement | null {
  return pageElementOf(target as Node | null)
}
