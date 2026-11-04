// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

/** 一个很小的“服务端”：保存节点数组，新增/删除/恢复都直接改它，get 返回当前状态。 */
const server = vi.hoisted(() => ({ nodes: [] as Array<Record<string, unknown>>, seq: 0 }))

const node = (id: string, parent: string | null, title: string, depth: number) => ({
  id, parent_id: parent, title, summary: '', kind: 'topic', kind_label: '主题', depth, child_count: 0, index: 0,
  origin: 'ai', user_edited: false, collapsed: false, x: null, y: null, refs: [], evidence: { figure_ids: [], equation_ids: [] }
})
const subtree = (id: string): string[] => {
  const kids = server.nodes.filter((n) => n.parent_id === id).map((n) => n.id as string)
  return [id, ...kids.flatMap(subtree)]
}
const tree = () => ({
  paper_id: 'p1', view: 'logic', view_label: '逻辑', status: 'ready', job_id: null, scope: null, model: null, error: null,
  verification: null, generated_at: null, nodes: server.nodes.map((n) => ({ ...n })),
  stats: { count: server.nodes.length, max_depth: 3, user_nodes: 0, edited_nodes: 0 }
})

const api = vi.hoisted(() => ({
  get: vi.fn(),
  overview: vi.fn(),
  node: { get: vi.fn(), addChild: vi.fn(), addSibling: vi.fn(), remove: vi.fn(), restore: vi.fn(), patch: vi.fn() },
  collapseAll: vi.fn()
}))

vi.mock('@/lib/api.mindmap', () => ({ mindmapApi: api }))
vi.mock('@/lib/api', () => ({ api: {}, errorMessage: (e: unknown) => String(e) }))
vi.mock('@/components/ui', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))
vi.mock('@/features/shared/download', () => ({ downloadText: vi.fn() }))
vi.mock('@/stores/jobsStore', () => ({ useJobsStore: { getState: () => ({ jobs: {} }), subscribe: () => () => undefined } }))

import { useMindmapStore } from './mindmapStore'

beforeEach(() => {
  server.nodes = [node('root', null, '根', 1), node('a', 'root', 'A', 2), node('a1', 'a', 'A1', 3), node('b', 'root', 'B', 2)]
  server.seq = 0
  api.get.mockReset().mockImplementation(async () => tree())
  api.overview.mockReset().mockResolvedValue({ views: {} })
  api.node.get.mockReset().mockImplementation(async (id: string) => ({ id, title: id, cards: [] }))
  api.node.addChild.mockReset().mockImplementation(async (parent: string, body: { title: string }) => {
    const id = `new${++server.seq}`
    server.nodes.push(node(id, parent, body.title, 3))
    return { id }
  })
  api.node.remove.mockReset().mockImplementation(async (id: string) => {
    const ids = subtree(id)
    const rows = server.nodes.filter((n) => ids.includes(n.id as string))
    const parent = rows.find((r) => r.id === id)!.parent_id as string
    server.nodes = server.nodes.filter((n) => !ids.includes(n.id as string))
    return { ok: true, removed: rows.length, parent_id: parent, snapshot: { parent_id: parent, index: 0, rows } }
  })
  api.node.restore.mockReset().mockImplementation(async (snap: { rows: Array<Record<string, unknown>> }) => {
    server.nodes.push(...snap.rows)
    return { ok: true, restored: snap.rows.length, id: snap.rows[0].id }
  })
  useMindmapStore.setState({ paperId: 'p1', view: 'logic', trees: { logic: tree() as never }, selectedId: null, detail: null, canUndo: false, canRedo: false })
})

const ids = (): string[] => server.nodes.map((n) => n.id as string).sort()

describe('思维导图：新增与删除节点的撤销/重做', () => {
  it('删除一个带子节点的分支后，撤销把整棵子树原样恢复，重做再删掉', async () => {
    const s = useMindmapStore.getState()
    expect(await s.deleteNode('a')).toBe(true)
    expect(ids()).toEqual(['b', 'root'])
    expect(useMindmapStore.getState().canUndo).toBe(true)
    await useMindmapStore.getState().undo()
    expect(ids()).toEqual(['a', 'a1', 'b', 'root']) // 子树 a 和 a1 一起回来
    expect(api.node.restore).toHaveBeenCalledTimes(1)
    expect(api.node.restore.mock.calls[0][0].rows.map((r: { id: string }) => r.id)).toEqual(['a', 'a1'])
    expect(useMindmapStore.getState().canRedo).toBe(true)
    await useMindmapStore.getState().redo()
    expect(ids()).toEqual(['b', 'root'])
  })

  it('新增节点后撤销会删掉它，重做用快照把同一个节点恢复回来', async () => {
    const d = await useMindmapStore.getState().addChild('root', '我的想法')
    expect(d?.id).toBe('new1')
    expect(ids()).toContain('new1')
    await useMindmapStore.getState().undo()
    expect(ids()).not.toContain('new1')
    await useMindmapStore.getState().redo()
    expect(ids()).toContain('new1') // 同一个 id，而不是新建一个
    expect(api.node.addChild).toHaveBeenCalledTimes(1)
  })

  it('撤销之后再做新操作会清空重做栈', async () => {
    await useMindmapStore.getState().deleteNode('b')
    await useMindmapStore.getState().undo()
    expect(useMindmapStore.getState().canRedo).toBe(true)
    await useMindmapStore.getState().addChild('root', 'X')
    expect(useMindmapStore.getState().canRedo).toBe(false)
  })

  it('恢复失败（例如父节点已被重新生成覆盖）时不假装成功，也不丢失撤销记录之外的状态', async () => {
    await useMindmapStore.getState().deleteNode('a')
    api.node.restore.mockRejectedValueOnce(new Error('节点已经存在'))
    await useMindmapStore.getState().undo()
    expect(ids()).toEqual(['b', 'root']) // 没有恢复出来
    expect(useMindmapStore.getState().canRedo).toBe(false) // 失败的撤销不进入重做栈
  })
})
