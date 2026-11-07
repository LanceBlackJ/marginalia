import type { Bookmark } from '@/lib/types'
import type { TextItem } from '@/lib/api.text'

const LABEL_MAX = 40
const QUOTE_MAX = 120

export interface PassageBookmark {
  page: number
  passage_ord: number
  /** 这一段开头的原文：重新解析后靠它找回位置 */
  quote: string
  /** 书签列表里显示的名字：这一段开头的一句 */
  label: string
}

/** 右键点中的这一段 → 要存的书签内容（页码、段落序号、开头的原文、显示名）。没有文字的块返回 null。 */
export function passageBookmark(parts: TextItem[]): PassageBookmark | null {
  const first = parts[0]
  if (!first) return null
  const text = parts
    .map((p) => p.text)
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return null
  const label = text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX)}…` : text
  return { page: first.page, passage_ord: first.ord, quote: text.slice(0, QUOTE_MAX), label }
}

/** 这一段（可能由几个被分页切开的块拼成）上已有的书签。 */
export function bookmarkOnPassage(bookmarks: Bookmark[], ords: number[]): Bookmark | undefined {
  return bookmarks.find((b) => b.passage_ord !== null && ords.includes(b.passage_ord))
}
