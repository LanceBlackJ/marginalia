import { describe, expect, it } from 'vitest'
import { foldTags, hasTag, normalizeTag, TAG_MAX } from './tags'

const t = (name: string) => ({ id: name, name })

describe('tags', () => {
  it('规范化输入：去空白、合并空格，空与超长被拒绝', () => {
    expect(normalizeTag('  图神经  网络 ')).toBe('图神经 网络')
    expect(normalizeTag('   ')).toBeNull()
    expect(normalizeTag('x'.repeat(TAG_MAX))).not.toBeNull()
    expect(normalizeTag('x'.repeat(TAG_MAX + 1))).toBeNull()
  })

  it('同名判断不区分大小写', () => {
    expect(hasTag([t('GNN')], 'gnn')).toBe(true)
    expect(hasTag([t('GNN')], 'cnn')).toBe(false)
  })

  it('多于 3 个标签时折叠，其余计入 +N', () => {
    const { visible, rest } = foldTags(['a', 'b', 'c', 'd', 'e'].map(t))
    expect(visible.map((x) => x.name)).toEqual(['a', 'b', 'c'])
    expect(rest.map((x) => x.name)).toEqual(['d', 'e'])
    expect(foldTags([t('a')]).rest).toEqual([])
  })
})
