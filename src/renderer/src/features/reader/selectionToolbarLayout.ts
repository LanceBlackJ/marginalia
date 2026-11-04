/** 划词工具条的定位（纯函数，便于测试）：优先放在选区上方，放不下放下方，左右按自身宽度夹在视口内。 */
export interface RectLike {
  left: number
  top: number
  right: number
  bottom: number
}

export interface Placement {
  left: number
  top: number
  place: 'above' | 'below' | 'inside'
}

const GAP = 8
const EDGE = 8
/** 顶部留给阅读器顶栏/状态条的高度：工具条不能压到它们 */
const TOP_SAFE = 56

export function placeSelectionToolbar(
  r: RectLike,
  tool: { w: number; h: number },
  view: { width: number; height: number }
): Placement {
  const center = (r.left + r.right) / 2
  const left = Math.min(Math.max(center - tool.w / 2, EDGE), Math.max(EDGE, view.width - tool.w - EDGE))
  const above = r.top - tool.h - GAP
  if (above >= TOP_SAFE) return { left, top: above, place: 'above' }
  const below = r.bottom + GAP
  if (below + tool.h <= view.height - EDGE) return { left, top: below, place: 'below' }
  // 选区占满整个视口高度：只能贴在视口内（此时无法完全不遮挡），放在可见部分的顶部
  return { left, top: Math.max(TOP_SAFE, Math.min(r.top + GAP, view.height - tool.h - EDGE)), place: 'inside' }
}
