import { describe, expect, it } from 'vitest'
import type { Bookmark } from '@/lib/types'
import type { TextItem } from '@/lib/api.text'
import { bookmarkOnPassage, passageBookmark } from './bookmarkAt'

const item = (ord: number, text: string, page = 3): TextItem => ({ ord, page, text, kind: 'paragraph' }) as TextItem
const bm = (id: string, passage_ord: number | null, page = 3): Bookmark => ({ id, page, label: '', created_at: '', passage_ord, quote: null, stale: false })

describe('passageBookmark', () => {
  it('取这一段开头的原文做引用，开头一句做显示名', () => {
    const long = 'We propose a low-rank adaptation method that freezes the pretrained weights and trains small matrices instead.'
    const r = passageBookmark([item(12, long)])!
    expect(r).toMatchObject({ page: 3, passage_ord: 12, quote: long })
    expect(r.label.endsWith('…')).toBe(true)
    expect(r.label.length).toBeLessThanOrEqual(41)
  })

  it('被分页切开的段落：以第一块为准，文字合在一起', () => {
    const r = passageBookmark([item(7, 'First half  of the\nparagraph', 4), item(8, 'second half.', 5)])!
    expect(r.page).toBe(4)
    expect(r.passage_ord).toBe(7)
    expect(r.quote).toBe('First half of the paragraph second half.')
  })

  it('没有文字的块不能加书签', () => {
    expect(passageBookmark([item(1, '   ')])).toBeNull()
    expect(passageBookmark([])).toBeNull()
  })
})

describe('bookmarkOnPassage', () => {
  it('只认落在这一段上的书签，整页书签不算', () => {
    const list = [bm('a', null), bm('b', 8)]
    expect(bookmarkOnPassage(list, [7, 8])?.id).toBe('b')
    expect(bookmarkOnPassage(list, [9])).toBeUndefined()
  })
})
