import { describe, expect, it } from 'vitest'
import type { CompareEdge } from '@/lib/types.discovery'
import { groupEdges, layoutGraph, NODE_W } from './graphLayout'

const edge = (from: string, to: string, relation: string, ai = false): CompareEdge => ({
  from, to, relation, relation_label: relation, basis: ai ? 'ai' : 'openalex', ai_inferred: ai, note: '', evidence: []
})

describe('groupEdges', () => {
  it('同方向的多条关系合并为一条连线，反方向不合并', () => {
    const g = groupEdges([edge('A', 'B', 'cites'), edge('A', 'B', 'improves', true), edge('B', 'A', 'cites')])
    expect(g.map((x) => x.id)).toEqual(['A>B', 'B>A'])
    expect(g[0].edges).toHaveLength(2)
  })
})

describe('layoutGraph', () => {
  const nodes = [
    { id: 'A', title: 'a', year: 2019 },
    { id: 'B', title: 'b', year: 2020 },
    { id: 'C', title: 'c', year: 2021 }
  ]
  it('有引用关系时按方向从左到右分层，节点不重叠', () => {
    const pos = layoutGraph(nodes, groupEdges([edge('A', 'B', 'cites'), edge('B', 'C', 'cites')]))
    expect(pos.get('A')!.x).toBeLessThan(pos.get('B')!.x)
    expect(pos.get('B')!.x).toBeLessThan(pos.get('C')!.x)
    expect(pos.get('B')!.x - pos.get('A')!.x).toBeGreaterThanOrEqual(NODE_W)
  })
  it('没有任何关系时所有节点都有位置，且不凭空画线', () => {
    const pos = layoutGraph(nodes, [])
    expect([...pos.keys()].sort()).toEqual(['A', 'B', 'C'])
    expect(groupEdges([])).toEqual([])
  })
  it('忽略指向不存在节点的边而不是报错', () => {
    expect(() => layoutGraph(nodes, groupEdges([edge('A', 'Z', 'cites')]))).not.toThrow()
  })
})
