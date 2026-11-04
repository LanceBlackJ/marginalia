import { describe, expect, it } from 'vitest'
import type { MindNode } from '@/lib/types.mindmap'
import { buildIndex, collapsedForDepth, descendantIds, indexInParent, layoutTree, maxDepth, navigate, nodeSize, planMove, textWidth, visibleNodes } from './tree'

let seq = 0
function node(id: string, parent: string | null, depth: number, ord: number, title = id, children = 0): MindNode {
  seq += 1
  return {
    id, paper_id: 'p', view: 'logic', parent_id: parent, ord, depth, child_count: children, title, summary: '', kind: 'topic',
    kind_label: '主题', origin: 'ai', user_edited: false, collapsed: false, x: null, y: null,
    evidence: { derived: false } as MindNode['evidence'], refs: [], created_at: `t${seq}`, updated_at: `t${seq}`
  }
}

/**
 * root
 * ├─ a (a1, a2)
 * ├─ b (b1 (b11))
 * └─ c
 */
const TREE: MindNode[] = [
  node('root', null, 1, 0, '根', 3),
  node('a', 'root', 2, 0, 'a', 2),
  node('b', 'root', 2, 1, 'b', 1),
  node('c', 'root', 2, 2, 'c', 0),
  node('a1', 'a', 3, 0, 'a1', 0),
  node('a2', 'a', 3, 1, 'a2', 0),
  node('b1', 'b', 3, 0, 'b1', 1),
  node('b11', 'b1', 4, 0, 'b11', 0)
]

describe('nodeSize', () => {
  it('根最大、层级越深框越轻，标题过长时折成两行', () => {
    const root = nodeSize({ title: '根', depth: 1 })
    const d2 = nodeSize({ title: '方法', depth: 2 })
    const d3 = nodeSize({ title: '方法', depth: 3 })
    expect(root.h).toBeGreaterThan(d2.h)
    expect(d2.h).toBeGreaterThan(d3.h)
    const long = nodeSize({ title: '这是一个非常非常长的节点标题用来测试折行'.repeat(2), depth: 3 })
    expect(long.h).toBeGreaterThan(d3.h)
    expect(long.w).toBeLessThanOrEqual(260)
  })
  it('中文按一个字号宽、英文按更窄估算', () => {
    expect(textWidth('中文', 14)).toBe(28)
    expect(textWidth('ab', 14)).toBeCloseTo(15.68, 2)
  })
})

describe('collapsedForDepth / maxDepth', () => {
  it('展开到第 N 层：深度 ≥ N 且有子节点的折叠', () => {
    expect(maxDepth(TREE)).toBe(4)
    expect([...collapsedForDepth(TREE, 2)].sort()).toEqual(['a', 'b', 'b1'])
    expect([...collapsedForDepth(TREE, 3)].sort()).toEqual(['b1'])
    expect(collapsedForDepth(TREE, 4).size).toBe(0)
    // 显示结果：第 2 层只看到根与它的直接子节点；第 1 层只剩根
    expect(visibleNodes(TREE, collapsedForDepth(TREE, 2)).map((n) => n.id)).toEqual(['root', 'a', 'b', 'c'])
    expect(visibleNodes(TREE, collapsedForDepth(TREE, 1)).map((n) => n.id)).toEqual(['root'])
    expect(visibleNodes(TREE, collapsedForDepth(TREE, 3)).map((n) => n.id)).toEqual(['root', 'a', 'a1', 'a2', 'b', 'b1', 'c'])
  })
})

describe('descendantIds / indexInParent', () => {
  it('后代不含自身，位置按父节点下的顺序', () => {
    const idx = buildIndex(TREE)
    expect([...descendantIds(idx, 'b')].sort()).toEqual(['b1', 'b11'])
    expect(descendantIds(idx, 'c').size).toBe(0)
    expect(indexInParent(TREE, 'b')).toBe(1)
    expect(indexInParent(TREE, 'root')).toBe(-1)
  })
})

describe('planMove', () => {
  it('拖到别的分支的节点上：成为它的最后一个子节点', () => {
    expect(planMove(TREE, 'c', 'a1', 0, 0)).toEqual({ parentId: 'a1', index: 0 }) // a1 还没有子节点
    expect(planMove(TREE, 'a1', 'b', 0, 0)).toEqual({ parentId: 'b', index: 1 }) // b 已有一个子节点 b1
  })
  it('拖到同级节点上：调整顺序，按上下半部分决定放前面还是后面', () => {
    expect(planMove(TREE, 'c', 'a', 10, 50)).toEqual({ parentId: 'root', index: 0 }) // 在 a 上方 → 放到 a 前面
    expect(planMove(TREE, 'a', 'b', 60, 50)).toEqual({ parentId: 'root', index: 1 }) // 去掉 a 后 b 在 0，放在它后面
  })
  it('不产生移动：根节点、拖到自己或自己的后代上、拖到自己的父节点上', () => {
    expect(planMove(TREE, 'root', 'a', 0, 0)).toBeNull()
    expect(planMove(TREE, 'b', 'b', 0, 0)).toBeNull()
    expect(planMove(TREE, 'b', 'b11', 0, 0)).toBeNull()
    expect(planMove(TREE, 'a1', 'a', 0, 0)).toBeNull()
    expect(planMove(TREE, 'x', 'a', 0, 0)).toBeNull()
  })
})

describe('navigate（键盘导航）', () => {
  const none = new Set<string>()
  it('← 父节点，→ 第一个子节点，↑↓ 同级', () => {
    expect(navigate(TREE, none, 'a1', 'parent')).toBe('a')
    expect(navigate(TREE, none, 'a', 'child')).toBe('a1')
    expect(navigate(TREE, none, 'a', 'next')).toBe('b')
    expect(navigate(TREE, none, 'b', 'prev')).toBe('a')
  })
  it('边界返回 null：根没有父/同级，最后一个没有下一个，折叠的节点不能直接去子节点', () => {
    expect(navigate(TREE, none, 'root', 'parent')).toBeNull()
    expect(navigate(TREE, none, 'root', 'next')).toBeNull()
    expect(navigate(TREE, none, 'c', 'next')).toBeNull()
    expect(navigate(TREE, none, 'a', 'prev')).toBeNull()
    expect(navigate(TREE, new Set(['a']), 'a', 'child')).toBeNull()
    expect(navigate(TREE, none, 'c', 'child')).toBeNull()
  })
})

describe('layoutTree', () => {
  const box = layoutTree(TREE)
  const cy = (id: string): number => box.get(id)!.y + box.get(id)!.h / 2
  it('同级严格按顺序从上到下，层级从左到右', () => {
    expect(cy('a')).toBeLessThan(cy('b'))
    expect(cy('b')).toBeLessThan(cy('c'))
    expect(cy('a1')).toBeLessThan(cy('a2'))
    expect(box.get('a')!.x).toBeGreaterThan(box.get('root')!.x)
    expect(box.get('a1')!.x).toBeGreaterThan(box.get('a')!.x)
    expect(box.get('b11')!.x).toBeGreaterThan(box.get('b1')!.x)
  })
  it('父节点居中于第一个和最后一个子节点之间', () => {
    expect(cy('a')).toBeCloseTo((cy('a1') + cy('a2')) / 2, 5)
    expect(cy('root')).toBeCloseTo((cy('a') + cy('c')) / 2, 5)
  })
  it('同一层的节点上下不重叠', () => {
    for (let d = 1; d <= 4; d += 1) {
      const row = TREE.filter((n) => n.depth === d).map((n) => box.get(n.id)!).sort((p, q) => p.y - q.y)
      for (let i = 1; i < row.length; i += 1) expect(row[i].y).toBeGreaterThanOrEqual(row[i - 1].y + row[i - 1].h)
    }
  })
  it('折叠后只对可见节点布局；保存过位置的节点沿用保存的位置', () => {
    const vis = visibleNodes(TREE, new Set(['a', 'b']))
    const small = layoutTree(vis)
    expect([...small.keys()].sort()).toEqual(['a', 'b', 'c', 'root'])
    const moved = TREE.map((n) => (n.id === 'c' ? { ...n, x: 500, y: 7 } : n))
    expect(layoutTree(moved).get('c')).toMatchObject({ x: 500, y: 7 })
  })
})
