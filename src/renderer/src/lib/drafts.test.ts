// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { getDraft, setDraft } from './drafts'

beforeEach(() => localStorage.clear())

describe('草稿', () => {
  it('写进去、读出来；清空或删除后没有了；不同论文互不影响', () => {
    setDraft('composer', 'p1', '没问完的话')
    setDraft('composer', 'p2', '另一篇的')
    expect(getDraft<string>('composer', 'p1')).toBe('没问完的话')
    expect(getDraft<string>('composer', 'p2')).toBe('另一篇的')
    setDraft('composer', 'p1', '')
    expect(getDraft('composer', 'p1')).toBeUndefined()
    expect(getDraft<string>('composer', 'p2')).toBe('另一篇的')
  })
  it('新笔记草稿保留引用和页码；每类草稿最多留 60 条，先丢最久没动的', () => {
    setDraft('note', 'p1', { text: '写了一半', quote: '原文一句', page: 3 })
    expect(getDraft<{ quote: string; page: number }>('note', 'p1')).toMatchObject({ quote: '原文一句', page: 3 })
    for (let i = 0; i < 70; i++) setDraft('edit', `n${i}`, `改${i}`)
    expect(getDraft('edit', 'n0')).toBeUndefined()
    expect(getDraft<string>('edit', 'n69')).toBe('改69')
  })
  it('存储不可用时不抛错', () => {
    const orig = Storage.prototype.setItem
    Storage.prototype.setItem = () => {
      throw new Error('quota')
    }
    try {
      expect(() => setDraft('composer', 'p', 'x')).not.toThrow()
    } finally {
      Storage.prototype.setItem = orig
    }
  })
})
