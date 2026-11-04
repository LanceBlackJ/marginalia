import { describe, expect, it } from 'vitest'
import type { GraphEdge, GraphNode, KbGraph } from '@/lib/api.export'
import { DEFAULT_FILTER, edgeStyle, filterGraph, layoutGraph, nodeSize } from './graphLayout'

const n = (id: string, type: GraphNode['type'], label = id): GraphNode => ({ id, type, label, kind: type === 'item' ? 'method' : null, ref: id })
const e = (id: string, source: string, target: string, type: GraphEdge['type'], status: GraphEdge['status'] = 'confirmed'): GraphEdge => ({
  id,
  source,
  target,
  type,
  label: type === 'relation' ? '改进了' : '',
  status
})
const g: KbGraph = {
  nodes: [n('item:1', 'item'), n('item:2', 'item'), n('item:3', 'item'), n('topic:1', 'topic'), n('paper:1', 'paper')],
  edges: [
    e('r1', 'item:1', 'item:2', 'relation'),
    e('r2', 'item:2', 'item:3', 'relation', 'suggested'),
    e('m1', 'item:1', 'topic:1', 'member'),
    e('s1', 'item:1', 'paper:1', 'source')
  ],
  counts: { items: 3, relations: 2, suggested: 1 }
}

describe('graph filter', () => {
  it('keeps everything by default', () => {
    const f = filterGraph(g, DEFAULT_FILTER)
    expect(f.nodes).toHaveLength(5)
    expect(f.edges).toHaveLength(4)
  })

  it('drops edges whose endpoint was filtered out', () => {
    const f = filterGraph(g, { ...DEFAULT_FILTER, types: new Set(['item']) })
    expect(f.nodes.map((x) => x.id)).toEqual(['item:1', 'item:2', 'item:3'])
    expect(f.edges.map((x) => x.id)).toEqual(['r1', 'r2'])
  })

  it('can show only academic relations', () => {
    const f = filterGraph(g, { ...DEFAULT_FILTER, hideStructure: true })
    expect(f.edges.every((x) => x.type === 'relation')).toBe(true)
  })

  it('hides isolated nodes', () => {
    const f = filterGraph(g, { ...DEFAULT_FILTER, hideStructure: true, onlyConnected: true })
    expect(f.nodes.map((x) => x.id).sort()).toEqual(['item:1', 'item:2', 'item:3'])
  })
})

describe('graph layout and edge style', () => {
  it('places every node without overlap', () => {
    const f = filterGraph(g, DEFAULT_FILTER)
    const placed = [...layoutGraph(f.nodes, f.edges).values()]
    expect(placed).toHaveLength(5)
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i]
        const b = placed[j]
        const overlap = a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
        expect(overlap).toBe(false)
      }
    }
  })

  it('does not crash on edges to unknown nodes', () => {
    const placed = layoutGraph([n('item:1', 'item')], [e('x', 'item:1', 'item:404', 'relation')])
    expect(placed.size).toBe(1)
  })

  it('makes longer titles taller, capped at three lines', () => {
    expect(nodeSize(n('a', 'item', '短')).height).toBeLessThan(nodeSize(n('b', 'item', '很长的标题'.repeat(20))).height)
    expect(nodeSize(n('b', 'item', 'x'.repeat(500))).height).toBe(nodeSize(n('c', 'item', 'x'.repeat(100))).height)
  })

  it('draws suggested relations dashed and marks them as pending', () => {
    expect(edgeStyle(g.edges[0])).toEqual({ dashed: false, strong: true, label: '改进了' })
    expect(edgeStyle(g.edges[1])).toEqual({ dashed: true, strong: false, label: '改进了 · 待确认' })
    expect(edgeStyle(g.edges[2])).toEqual({ dashed: false, strong: false, label: '' })
  })
})
