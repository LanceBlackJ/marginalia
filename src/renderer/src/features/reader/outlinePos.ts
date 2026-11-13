/**
 * 左边目录里“当前在读的章节”：以前只看页码（这一页上最后一个开始的章节），一页里有好几个小节时会高亮到后面还没读到的那个。
 * 现在看阅读线所在的段落（ord）：最后一个“起点已经在阅读线之前”的章节；起点就是这个章节标题下的第一个段落。
 */
import type { OutlineItem } from '@/lib/types'

export interface PosItem {
  ord: number
  page: number
  section_title: string | null
  bbox: [number, number, number, number] | number[]
}

/** PDF 里阅读线（页内 y，单位 pt）所在的段落：这一页里底边还没越过阅读线的第一个；都越过了就取这一页最后一个。 */
export function ordAtPdfPoint(items: readonly PosItem[], page: number, y: number): number | null {
  let last: number | null = null
  for (const it of items) {
    if (it.page !== page) continue
    last = it.ord
    if (it.bbox[3] >= y) return it.ord
  }
  return last
}

/** 每个目录项的起点：该标题下第一个段落的 ord；找不到（标题对不上）的留 null，退回按页码判断。 */
export function sectionStarts(outline: readonly OutlineItem[], items: readonly PosItem[]): Array<number | null> {
  const first = new Map<string, number>()
  for (const it of items) if (it.section_title && !first.has(it.section_title)) first.set(it.section_title, it.ord)
  return outline.map((o) => first.get(o.title) ?? null)
}

/**
 * 正文里“阅读线上方最近的标题”：标题按出现顺序给出，取最后一个顶部已越过阅读线的；一个都没越过就取第一个。
 * 标题是按正文里实际的位置判断的（小节的标题在大章节的标题之后），所以停在小节里就得到小节，不会停在大章节上。
 */
export function activeSectionAt(tops: ReadonlyArray<{ title: string; top: number }>, line: number): string | null {
  if (tops.length === 0) return null
  // 不能指望标题在页面上的位置严格按顺序：只要有一个排在前面的标题位置偏下，“遇到就停”会让高亮卡在更早的小节。
  // 所以看全部标题：阅读线上方最近的那个；都在阅读线下面就取最靠上的。
  let best: { title: string; top: number } | null = null
  for (const t of tops) if (t.top <= line && (!best || t.top >= best.top)) best = t
  if (best) return best.title
  return tops.reduce((m, t) => (t.top < m.top ? t : m)).title
}

/**
 * 当前章节在目录里的下标。优先用 sectionTitle（文字阅读器里按标题的实际位置算出来的当前小节）；
 * 没有它（PDF 视图、还没滚动过）或目录里找不到时，用阅读位置的段落序号；再不行退回“起始页不超过当前页的最后一项”。
 */
export function activeOutlineIndex(
  outline: readonly OutlineItem[],
  items: readonly PosItem[],
  anchorOrd: number | null,
  currentPage: number,
  sectionTitle: string | null = null
): number {
  if (sectionTitle) {
    // 先按章节键（#id，同名小节靠它区分），再按标题
    const byKey = sectionTitle.startsWith('#') ? outline.findIndex((o) => `#${o.id}` === sectionTitle) : -1
    if (byKey >= 0) return byKey
    const hit = outline.findIndex((o) => o.title === sectionTitle)
    if (hit >= 0) return hit
  }
  const byPage = (): number => {
    let idx = -1
    outline.forEach((o, i) => {
      if (o.page <= currentPage) idx = i
    })
    return idx
  }
  if (anchorOrd === null || items.length === 0) return byPage()
  const starts = sectionStarts(outline, items)
  if (starts.every((s) => s === null)) return byPage()
  let idx = -1
  starts.forEach((s, i) => {
    if (s !== null && s <= anchorOrd) idx = i
  })
  return idx >= 0 ? idx : byPage()
}

/**
 * 滚动时左边目录该高亮哪一节：平时是阅读线上方最近的标题；滚到文档最底部时，最后一节（参考文献等）的标题已经不可能再顶到阅读线上，
 * 此时取最后一节；但如果是刚从目录点过去的章节、它的标题还在窗口里，就优先它（点“致谢”，它和后面的“参考文献”同屏时，高亮不能被抢走）。
 */
export function activeSectionOnScroll(
  tops: ReadonlyArray<{ title: string; top: number }>,
  line: number,
  atBottom: boolean,
  jumpedTitle: string | null,
  viewHeight: number
): string | null {
  if (!atBottom || tops.length === 0) return activeSectionAt(tops, line)
  if (jumpedTitle) {
    const hit = tops.find((t) => t.title === jumpedTitle)
    if (hit && hit.top > -40 && hit.top < viewHeight - 20) return jumpedTitle
  }
  return tops[tops.length - 1].title
}
