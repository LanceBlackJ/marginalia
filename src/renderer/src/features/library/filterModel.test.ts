import { describe, expect, it } from 'vitest'
import type { FolderItem } from '@/lib/types.library'
import {
  buildFolderTree,
  canNestFolder,
  descendantIds,
  dragPaperIds,
  filterChips,
  filtersToQuery,
  flattenTree,
  folderPath,
  hasTextQuery,
  isEmptyFilters,
  nextDay,
  normalizeFilters,
  parseFilters,
  reorderIds,
  serializeFilters,
  splitSnippet, groupByTag } from './filterModel'

const f = (id: string, name: string, parent: string | null = null, sort = 0): FolderItem => ({
  id,
  name,
  parent_id: parent,
  sort,
  paper_count: 0,
  total_count: 0
})

describe('筛选条件', () => {
  it('去掉空值，只保留“必须有”的标记', () => {
    expect(normalizeFilters({ note_q: '  ', author: ' Li ', tags: [], has_notes: false, favorite: true, year_from: NaN })).toEqual({ author: 'Li', favorite: true })
    expect(isEmptyFilters({ note_q: '', tags: [] })).toBe(true)
    expect(isEmptyFilters({ has_code: true })).toBe(false)
  })

  it('转成后端查询参数：标签合并、导入日期含当天', () => {
    const q = filtersToQuery({ note_q: 'attention', tags: ['a', 'b'], tag_mode: 'all', year_from: 2019, has_notes: true, imported_to: '2024-02-29' })
    expect(q).toEqual({ note_q: 'attention', tags: 'a,b', tag_mode: 'all', year_from: 2019, has_notes: true, imported_to: '2024-03-01' })
    expect(filtersToQuery({ tags: ['a'] })).toEqual({ tags: 'a', tag_mode: 'any' })
    expect(filtersToQuery({})).toEqual({})
    expect(nextDay('2023-12-31')).toBe('2024-01-01')
    expect(nextDay('不是日期')).toBe('不是日期')
  })

  it('URL 序列化往返一致，坏数据当作没有条件', () => {
    const src = { note_q: '机制', tags: ['t1'], tag_mode: 'all' as const, read: 'done' as const, has_code: true, year_to: 2022 }
    expect(parseFilters(serializeFilters(src))).toEqual(src)
    expect(serializeFilters({})).toBe('')
    expect(parseFilters('{坏的 json')).toEqual({})
    expect(parseFilters('[1,2]')).toEqual({})
    expect(parseFilters(JSON.stringify({ read: 'bogus', parse: 'bogus', has_notes: 'yes', year_from: '2020', note_q: 5 }))).toEqual({})
    expect(parseFilters(null)).toEqual({})
  })

  it('有文字类条件时才需要按相关度排序', () => {
    expect(hasTextQuery({}, '')).toBe(false)
    expect(hasTextQuery({}, 'graph')).toBe(true)
    expect(hasTextQuery({ note_q: 'x' }, '')).toBe(true)
    expect(hasTextQuery({ year_from: 2020 }, '')).toBe(false)
  })
})

describe('筛选芯片', () => {
  const lookup = { tagName: (id: string) => `标签${id}`, folderPath: (id: string) => `夹/${id}` }
  it('每个条件一枚芯片，移除后只去掉对应条件', () => {
    const cond = { note_q: 'attention', tags: ['1', '2'], year_from: 2019, year_to: 2021, has_notes: true, folder: 'x' }
    const chips = filterChips(cond, lookup)
    expect(chips.map((c) => c.key)).toEqual(['folder', 'note_q', 'tags', 'year', 'has_notes'])
    expect(chips.find((c) => c.key === 'year')?.label).toBe('年份：2019–2021 年')
    expect(chips.find((c) => c.key === 'tags')?.label).toBe('标签：标签1 / 标签2 · 任一')
    expect(chips.find((c) => c.key === 'folder')?.label).toBe('文件夹：夹/x')
    const rest = chips.find((c) => c.key === 'year')!.without
    expect(rest.year_from).toBeUndefined()
    expect(rest.year_to).toBeUndefined()
    expect(rest.note_q).toBe('attention')
    expect(filterChips({ folder: 'none' }, lookup)[0].label).toBe('文件夹：未分类')
    expect(filterChips({ year_from: 2020 }, lookup)[0].label).toBe('年份：2020 年起')
    expect(filterChips({ tags: ['1', '2'], tag_mode: 'all' }, lookup)[0].label).toContain(' · 全部')
  })

  it('过长的关键词被截短', () => {
    expect(filterChips({ text_q: '一二三四五六七八九十一二三四五六七八九十' }, lookup)[0].label).toBe('全文：一二三四五六七八九十一二三四五六七八…')
  })
})

describe('文件夹树', () => {
  // 同级的 sort 在后端是递增分配的；这里同样给出明确的顺序
  const items = [f('a', '图', null, 0), f('b', '注意力', 'a', 1), f('c', '卷积', 'a', 0), f('d', '视觉', null, 1), f('orphan', '孤儿', 'gone', 2)]

  it('按 sort、名称排序并带上层级；找不到父级的挂到最上层', () => {
    const tree = buildFolderTree(items)
    expect(tree.map((n) => n.id)).toEqual(['a', 'd', 'orphan'])
    expect(tree[0].children.map((n) => n.id)).toEqual(['c', 'b'])
    expect(tree[0].children[0].depth).toBe(1)
  })

  it('只展开已展开的节点', () => {
    const tree = buildFolderTree(items)
    expect(flattenTree(tree, new Set()).map((n) => n.id)).toEqual(['a', 'd', 'orphan'])
    expect(flattenTree(tree, new Set(['a'])).map((n) => n.id)).toEqual(['a', 'c', 'b', 'd', 'orphan'])
  })

  it('子孙与路径', () => {
    expect(descendantIds(items, 'a').sort()).toEqual(['a', 'b', 'c'])
    expect(folderPath(items, 'b')).toBe('图 / 注意力')
    expect(folderPath(items, 'nope')).toBe('')
  })

  it('不能把文件夹放进自己或子孙里，也不能超过最大层数', () => {
    expect(canNestFolder(items, 'a', 'a')).toBe(false)
    expect(canNestFolder(items, 'a', 'b')).toBe(false)
    expect(canNestFolder(items, 'b', 'd')).toBe(true)
    expect(canNestFolder(items, 'b', null)).toBe(true)
    expect(canNestFolder(items, 'b', 'a')).toBe(true) // 本来就在那里
    const deep = [f('1', '1'), f('2', '2', '1'), f('3', '3', '2'), f('4', '4', '3'), f('5', '5', '4'), f('6', '6', '5'), f('x', 'x'), f('y', 'y', 'x')]
    expect(canNestFolder(deep, 'x', '6')).toBe(false) // x 带着子级 y 放到第 6 层下面会变成 8 层
    expect(canNestFolder(deep, 'y', '4')).toBe(true)
  })

  it('同级重排', () => {
    expect(reorderIds(['a', 'b', 'c'], 'c', 'a')).toEqual(['c', 'a', 'b'])
    expect(reorderIds(['a', 'b', 'c'], 'a', null)).toEqual(['b', 'c', 'a'])
    expect(reorderIds(['a', 'b', 'c'], 'b', 'zzz')).toEqual(['a', 'c', 'b'])
  })
})

describe('拖拽与命中片段', () => {
  it('拖拽已选中的论文就带着整个选择，否则只拖它自己', () => {
    expect(dragPaperIds('p1', new Set(['p1', 'p2']))).toEqual(['p1', 'p2'])
    expect(dragPaperIds('p3', new Set(['p1', 'p2']))).toEqual(['p3'])
    expect(dragPaperIds('p3', new Set())).toEqual(['p3'])
  })

  it('命中片段切成前、中、后三段', () => {
    expect(splitSnippet('…这里的 Attention 机制…', [5, 14])).toEqual(['…这里的 ', 'Attention', ' 机制…'])
    expect(splitSnippet('短', [-3, 99])).toEqual(['', '短', ''])
    expect(splitSnippet('abc', [2, 1])).toEqual(['ab', '', 'c'])
  })
})


describe('groupByTag', () => {
  const t = (id: string) => ({ id, name: id })
  it('一篇有几个标签就出现在几组里，没标签的放最后，组按篇数从多到少', () => {
    const items = [
      { id: 'a', tags: [t('X'), t('Y')] },
      { id: 'b', tags: [t('Y')] },
      { id: 'c', tags: [] },
      { id: 'd' }
    ]
    const g = groupByTag(items)
    expect(g.map((x) => [x.name, x.items.map((i) => i.id)])).toEqual([
      ['Y', ['a', 'b']],
      ['X', ['a']],
      ['无标签', ['c', 'd']]
    ])
  })
})
