import { describe, expect, it } from 'vitest'
import type { Note } from '@/lib/types'
import { noteMarksByPage, noteOrdMap } from './noteMarks'

const note = (o: Partial<Note>): Note => ({
  id: 'n1', paper_id: 'p', page: 2, quote: null, passage_ord: null, text: '想法', card_id: null, created_at: '', updated_at: '', ...o
})

describe('正文里的笔记标记', () => {
  it('只有引用了原文、有页码的笔记才会标在正文里，按页分组', () => {
    const m = noteMarksByPage([note({ id: 'a', quote: 'graph attention' }), note({ id: 'b', quote: null }), note({ id: 'c', page: null, quote: 'x y' }), note({ id: 'd', page: 3, quote: 'multi-agent' })])
    expect(m.get(2)).toEqual([{ id: 'note:a', text: 'graph attention', color: 'note' }])
    expect(m.get(3)?.[0].id).toBe('note:d')
    expect(m.size).toBe(2)
  })

  it('每条笔记归到段落：记了段落序号的用序号，否则在这一页里找包含引用的段落（译文视图靠这个贴角标）', () => {
    const items = [
      { ord: 10, page: 2, text: 'We study graph attention networks on large graphs.' },
      { ord: 11, page: 2, text: 'Another paragraph.' },
      { ord: 12, page: 3, text: 'We study graph attention networks again.' }
    ]
    const byQuote = noteOrdMap([note({ id: 'a', quote: 'Graph  Attention' })], items)
    expect([...byQuote.keys()]).toEqual([10]) // 空白和大小写不影响，只在这一页找
    const byOrd = noteOrdMap([note({ id: 'b', passage_ord: 11, quote: 'whatever' })], items)
    expect([...byOrd.keys()]).toEqual([11])
    expect(noteOrdMap([note({ id: 'c', quote: 'not in the text' })], items).size).toBe(0)
  })
})
