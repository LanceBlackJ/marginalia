import { describe, expect, it } from 'vitest'
import type { FrameworkData } from '@/lib/types.vision'
import { layoutFramework, moduleSize, textWidth } from './frameworkLayout'

const mod = (id: string, label: string, over: Partial<FrameworkData['modules'][number]> = {}): FrameworkData['modules'][number] => ({
  id, label, role: null, kind: 'process', group: null, in_text: true, ...over
})

const data = (over: Partial<FrameworkData> = {}): FrameworkData => ({
  summary: '', modules: [], flows: [], groups: [], uncertain: [], verifiable: true, warnings: [], ...over
})

describe('框架图布局', () => {
  it('估算文字宽度：中文比英文宽，节点宽度有上下限', () => {
    expect(textWidth('编码器')).toBeGreaterThan(textWidth('abc'))
    expect(moduleSize('a').w).toBe(92)
    expect(moduleSize('x'.repeat(100)).w).toBe(220)
    expect(moduleSize('长'.repeat(30)).h).toBeGreaterThan(moduleSize('短').h)
  })

  it('从左到右排版：有连线的模块依次靠右，节点互不重叠', () => {
    const d = data({
      modules: [mod('a', 'Input'), mod('b', 'Encoder'), mod('c', 'Decoder')],
      flows: [{ from: 'a', to: 'b', label: '', kind: 'data' }, { from: 'b', to: 'c', label: 'embeddings', kind: 'data' }]
    })
    const l = layoutFramework(d)
    const at = (id: string) => l.modules.find((m) => m.id === id)!
    expect(at('a').x).toBeLessThan(at('b').x)
    expect(at('b').x).toBeLessThan(at('c').x)
    expect(l.edges.map((e) => e.label)).toEqual(['', 'embeddings'])
    expect(l.width).toBeGreaterThan(0)
    expect(l.height).toBeGreaterThan(0)
  })

  it('丢弃指向不存在模块的连线、自环和重复连线，并如实计数；不编造节点', () => {
    const d = data({
      modules: [mod('a', 'A'), mod('b', 'B')],
      flows: [
        { from: 'a', to: 'b', label: '', kind: 'data' },
        { from: 'a', to: 'b', label: 'dup', kind: 'data' },
        { from: 'a', to: 'zzz', label: '', kind: 'data' },
        { from: 'b', to: 'b', label: '', kind: 'control' }
      ]
    })
    const l = layoutFramework(d)
    expect(l.edges).toHaveLength(1)
    expect(l.droppedEdges).toBe(3)
    expect(l.modules.map((m) => m.id)).toEqual(['a', 'b'])
  })

  it('图的文字层里没找到的模块：相连的连线标为不确定（虚线）', () => {
    const d = data({
      modules: [mod('a', 'A'), mod('b', 'Ghost', { in_text: false }), mod('c', 'C', { in_text: null })],
      flows: [{ from: 'a', to: 'b', label: '', kind: 'data' }, { from: 'a', to: 'c', label: '', kind: 'data' }]
    })
    const l = layoutFramework(d)
    expect(l.edges.find((e) => e.to === 'b')!.uncertain).toBe(true)
    expect(l.edges.find((e) => e.to === 'c')!.uncertain).toBe(false) // 位图无法核对不等于不确定
  })

  it('分组：成员的位置相对分组左上角，分组框包住它的成员', () => {
    const d = data({
      modules: [mod('a', 'Synthetic graphs'), mod('b', 'f_E'), mod('c', 'A real network'), mod('d', 'f_D')],
      groups: [{ id: 'g1', label: 'Offline training', members: ['a', 'b'] }, { id: 'g2', label: 'Real-world application', members: ['c', 'd'] }],
      flows: [{ from: 'a', to: 'b', label: '', kind: 'data' }, { from: 'c', to: 'd', label: '', kind: 'data' }]
    })
    const l = layoutFramework(d)
    expect(l.groups.map((g) => g.id).sort()).toEqual(['g1', 'g2'])
    for (const m of l.modules) {
      const g = l.groups.find((x) => x.id === m.group)!
      expect(g).toBeTruthy()
      expect(m.x).toBeGreaterThanOrEqual(0)
      expect(m.y).toBeGreaterThanOrEqual(0)
      expect(m.x + m.w).toBeLessThanOrEqual(g.w + 0.5)
      expect(m.y + m.h).toBeLessThanOrEqual(g.h + 0.5)
    }
  })

  it('没有成员的分组不画；模块的 group 字段也能把它放进分组', () => {
    const d = data({
      modules: [mod('a', 'A', { group: 'g1' }), mod('b', 'B')],
      groups: [{ id: 'g1', label: 'G1', members: [] }, { id: 'g2', label: 'Empty', members: [] }],
      flows: [{ from: 'a', to: 'b', label: '', kind: 'data' }]
    })
    const l = layoutFramework(d)
    expect(l.groups.map((g) => g.id)).toEqual(['g1'])
    expect(l.modules.find((m) => m.id === 'a')!.group).toBe('g1')
    expect(l.modules.find((m) => m.id === 'b')!.group).toBeNull()
  })

  it('没有模块或没有连线时不会报错', () => {
    expect(layoutFramework(data()).modules).toEqual([])
    const l = layoutFramework(data({ modules: [mod('a', 'A'), mod('b', 'B')] }))
    expect(l.modules).toHaveLength(2)
    expect(l.edges).toEqual([])
  })
})
