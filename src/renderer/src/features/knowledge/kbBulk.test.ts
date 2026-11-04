import { describe, expect, it } from 'vitest'
import { allSelected, bulkSummary, pruneSelected, selectRange, toggleSelected } from './kbBulk'

const order = ['a', 'b', 'c', 'd', 'e']

describe('kbBulk', () => {
  it('toggles without mutating the original set', () => {
    const s = new Set(['a'])
    const t = toggleSelected(s, 'b')
    expect([...s]).toEqual(['a'])
    expect([...t].sort()).toEqual(['a', 'b'])
    expect([...toggleSelected(t, 'a')]).toEqual(['b'])
  })

  it('selects a range in list order, in either direction', () => {
    expect([...selectRange(order, new Set(), 'b', 'd')].sort()).toEqual(['b', 'c', 'd'])
    expect([...selectRange(order, new Set(), 'd', 'b')].sort()).toEqual(['b', 'c', 'd'])
    expect([...selectRange(order, new Set(['a']), 'c', 'd')].sort()).toEqual(['a', 'c', 'd'])
  })

  it('falls back to a plain toggle without an anchor or for unknown ids', () => {
    expect([...selectRange(order, new Set(), null, 'c')]).toEqual(['c'])
    expect([...selectRange(order, new Set(['a']), 'a', 'zzz')]).toEqual(['a'])
  })

  it('drops selections that left the list', () => {
    expect([...pruneSelected(new Set(['a', 'x']), order)]).toEqual(['a'])
    const same = new Set(['a'])
    expect(pruneSelected(same, order)).toBe(same)
  })

  it('knows when everything is selected', () => {
    expect(allSelected(new Set(order), order)).toBe(true)
    expect(allSelected(new Set(['a']), order)).toBe(false)
    expect(allSelected(new Set(), [])).toBe(false)
  })

  it('summarises bulk results honestly, including skipped ones', () => {
    expect(bulkSummary('delete', { affected: 3, skipped: 0 })).toBe('3 个条目已删除')
    expect(bulkSummary('add_topic', { affected: 2, skipped: 1 })).toBe('2 个条目已加入主题，1 个已不存在而跳过')
  })
})
