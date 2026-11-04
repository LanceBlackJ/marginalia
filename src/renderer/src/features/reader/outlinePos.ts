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

/** 当前章节在目录里的下标；没有阅读位置或目录对不上时，退回“起始页不超过当前页的最后一项”。 */
export function activeOutlineIndex(outline: readonly OutlineItem[], items: readonly PosItem[], anchorOrd: number | null, currentPage: number): number {
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
