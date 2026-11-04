import { normalizeSelectionText, type SelectionInfo } from '@/features/reader/pdf/selection'

function elementOf(node: Node | null): HTMLElement | null {
  return node && node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement | null)
}

/**
 * 读取文字阅读器里的选区，转成选区工具条用的 SelectionInfo。
 * 页码取选区起点所在页，段落序号取起点所在段落；文字视图没有 PDF 坐标，所以 rects 为空、fromText 为 true。
 */
export function readTextSelection(root: HTMLElement): SelectionInfo | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
  const range = sel.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return null
  const start = elementOf(range.startContainer)
  const pageEl = start?.closest<HTMLElement>('[data-page]')
  if (!pageEl) return null
  const text = normalizeSelectionText(sel.toString())
  if (!text) return null
  const ordEl = start?.closest<HTMLElement>('[data-ord]')
  const bounds = range.getBoundingClientRect()
  return {
    text,
    page: Number(pageEl.dataset.page),
    rects: [],
    clientRect: { left: bounds.left, top: bounds.top, right: bounds.right, bottom: bounds.bottom },
    ord: ordEl?.dataset.ord !== undefined ? Number(ordEl.dataset.ord) : undefined,
    fromText: true
  }
}
