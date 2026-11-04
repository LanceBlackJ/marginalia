import type { Note } from '@/lib/types'
import type { AnnMark } from '@/features/text/textModel'

export const NOTE_MARK_PREFIX = 'note:'

/** 带引用原文的笔记：按页码、逐字匹配标到正文里（和高亮一样，匹配不到的不标）。 */
export function noteMarksByPage(notes: Note[]): Map<number, AnnMark[]> {
  const m = new Map<number, AnnMark[]>()
  for (const n of notes) {
    const q = (n.quote ?? '').trim()
    if (!n.page || q.length < 2) continue
    m.set(n.page, [...(m.get(n.page) ?? []), { id: `${NOTE_MARK_PREFIX}${n.id}`, text: q, color: 'note' }])
  }
  return m
}

const squash = (s: string): string => s.replace(/\s+/g, ' ').trim().toLowerCase()

/**
 * 每条笔记属于哪个段落：记了段落序号的用它；只有页码和引用原文的，在这一页里找包含这段引用的段落。
 * 译文视图里没法在文字上标出引用，就在对应段落上贴一张便签角标。
 */
export function noteOrdMap(notes: Note[], items: Array<{ ord: number; page: number; text: string }>): Map<number, Note[]> {
  const out = new Map<number, Note[]>()
  const add = (ord: number, n: Note): void => {
    out.set(ord, [...(out.get(ord) ?? []), n])
  }
  for (const n of notes) {
    if (n.passage_ord !== null && n.passage_ord !== undefined && items.some((i) => i.ord === n.passage_ord)) {
      add(n.passage_ord, n)
      continue
    }
    const q = squash((n.quote ?? '').slice(0, 120))
    if (!n.page || q.length < 2) continue
    const hit = items.find((i) => i.page === n.page && squash(i.text).includes(q))
    if (hit) add(hit.ord, n)
  }
  return out
}
