/** 划词翻译的纯函数：哪些选中内容提供翻译、选区在哪个可翻译容器里、浮条与译文浮层的定位。 */

/** 后端 /ai/translate 单次上限是 8000 字；这里留一点余量。 */
export const MAX_TRANSLATE_CHARS = 6000

export interface Box {
  left: number
  right: number
  top: number
  bottom: number
}

export interface Viewport {
  w: number
  h: number
}

/** 汉字占“汉字 + 拉丁字母”的比例。 */
export function cjkRatio(text: string): number {
  const cjk = (text.match(/[一-鿿]/g) ?? []).length
  const latin = (text.match(/[A-Za-z]/g) ?? []).length
  return cjk + latin === 0 ? 0 : cjk / (cjk + latin)
}

/** 提供“翻译”的条件：至少 2 个拉丁字母的单词、汉字不占多数、不超长。中文为主的选中内容不显示翻译按钮。 */
export function shouldOfferTranslate(text: string): boolean {
  const t = text.trim()
  if (t.length < 3 || t.length > MAX_TRANSLATE_CHARS) return false
  if (cjkRatio(t) > 0.5) return false
  return /[A-Za-z]{2,}/.test(t)
}

/** 选中文字所在的可翻译容器（带 data-translatable 属性的最近祖先）。 */
export function translatableContainer(node: Node | null): HTMLElement | null {
  let n: Node | null = node
  while (n) {
    if (n.nodeType === 1 && (n as HTMLElement).hasAttribute('data-translatable')) return n as HTMLElement
    n = n.parentNode
  }
  return null
}

/** 选区的起止都在同一个可翻译容器里才算（跨容器的选区不处理）。 */
export function selectionContainer(sel: Selection | null): HTMLElement | null {
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null
  const a = translatableContainer(sel.anchorNode)
  const b = translatableContainer(sel.focusNode)
  return a && a === b ? a : null
}

/** 浮条定位：优先放选区上方（不遮挡选中文字），上方放不下放下方；左右夹在视口内。 */
export function placeBar(rect: Box, size: { w: number; h: number }, vp: Viewport): { left: number; top: number } {
  const gap = 8
  const center = (rect.left + rect.right) / 2
  const left = Math.min(Math.max(center - size.w / 2, 8), Math.max(8, vp.w - size.w - 8))
  const above = rect.top - size.h - gap
  if (above >= 8) return { left, top: above }
  return { left, top: Math.min(rect.bottom + gap, Math.max(8, vp.h - size.h - 8)) }
}

export interface PanelPlacement {
  left: number
  top?: number
  bottom?: number
  maxHeight: number
}

/** 译文浮层定位：放选区下方，下方空间不够放上方；左右夹在视口内。 */
export function placePanel(rect: Box, width: number, vp: Viewport, height = 220): PanelPlacement {
  const center = (rect.left + rect.right) / 2
  const left = Math.min(Math.max(center - width / 2, 8), Math.max(8, vp.w - width - 8))
  const below = vp.h - rect.bottom - 14
  const above = rect.top - 14
  if (below >= Math.min(height, 160) || below >= above) return { left, top: rect.bottom + 8, maxHeight: Math.max(120, below - 8) }
  return { left, bottom: vp.h - rect.top + 8, maxHeight: Math.max(120, above - 8) }
}
