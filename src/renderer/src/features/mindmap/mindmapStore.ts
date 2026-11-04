/**
 * 思维导图共享状态：阅读器右侧面板与工作区页面共用同一份数据（同一篇论文打开两处看到的是同一棵树）。
 * 编辑（改名/增删/移动）先写后端再更新本地；折叠与拖动位置是视图状态，本地先变、再异步保存。
 */
import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import { mindmapApi, type ExportFormat, type NodeSnapshot } from '@/lib/api.mindmap'
import type { InsightCard } from '@/lib/types'
import {
  MIND_VIEWS,
  type ExplainState,
  type MindNode,
  type MindNodeDetail,
  type MindNodePatch,
  type MindOverview,
  type MindSearchHit,
  type MindTree,
  type MindView
} from '@/lib/types.mindmap'
import { toast } from '@/components/ui'
import { downloadText } from '@/features/shared/download'
import { useJobsStore } from '@/stores/jobsStore'
import { collapsedForDepth, expandTo, indexInParent, initialCollapsed, mergeCollapsed, patchLocal, removeBranch } from './tree'

export interface JobProgress {
  progress: number
  message: string
}

interface MindState {
  paperId: string | null
  view: MindView
  trees: Partial<Record<MindView, MindTree>>
  overview: MindOverview | null
  loading: boolean
  error: string | null
  collapsed: Partial<Record<MindView, Set<string>>>
  selectedId: string | null
  detail: MindNodeDetail | null
  /** 画布据此把视野移到某个节点；seq 递增才触发 */
  focus: { id: string; seq: number } | null
  query: string
  hits: MindSearchHit[]
  kindFilter: Set<string>
  progress: Partial<Record<MindView, JobProgress>>
  explain: Record<string, ExplainState>
  /** 画布据此让某个节点进入改名状态；seq 递增才触发 */
  editRequest: { id: string; seq: number } | null
  canUndo: boolean
  canRedo: boolean

  open: (paperId: string, opts?: { view?: MindView; nodeId?: string | null }) => Promise<void>
  reset: () => void
  setView: (view: MindView) => Promise<void>
  reload: (view?: MindView) => Promise<void>
  select: (id: string | null, opts?: { focus?: boolean }) => Promise<void>
  toggleCollapse: (id: string) => void
  setAllCollapsed: (collapsed: boolean) => void
  generate: (view: MindView | 'all', branchId?: string) => Promise<boolean>
  patchNode: (id: string, patch: MindNodePatch, opts?: { record?: boolean }) => Promise<boolean>
  /** 移动节点：成为 parentId 的子节点，插在第 index 位（去掉自身后的位置）。可撤销。 */
  moveNode: (id: string, parentId: string, index: number) => Promise<boolean>
  /** 新增“新节点”并立刻进入改名状态（Tab / Enter 快捷键用）。 */
  quickAdd: (mode: 'child' | 'sibling', id: string) => Promise<void>
  requestEdit: (id: string) => void
  undo: () => Promise<void>
  redo: () => Promise<void>
  /** 展开到第 depth 层（深度 ≥ depth 且有子节点的折叠）；Infinity 表示全部展开。 */
  collapseToDepth: (depth: number) => void
  addChild: (parentId: string, title: string) => Promise<MindNodeDetail | null>
  addSibling: (id: string, title: string) => Promise<MindNodeDetail | null>
  deleteNode: (id: string) => Promise<boolean>
  commitPositions: (items: Array<{ id: string; x: number; y: number }>) => Promise<void>
  resetLayout: () => Promise<void>
  runSearch: (q: string) => Promise<void>
  toggleKind: (kind: string) => void
  clearKinds: () => void
  exportView: (format: ExportFormat) => Promise<void>
  explainNode: (id: string, question?: string) => Promise<void>
  stopExplain: () => void
}

let pollTimer: number | undefined
let searchSeq = 0
let focusSeq = 0
let explainCtl: AbortController | null = null
let detailSeq = 0
let editSeq = 0

/** 撤销/重做：覆盖“改标题/摘要/类型”“移动节点”“新增节点”“删除节点（含子树）”。删除/新增的撤销靠后端返回的子树快照原样恢复（同样的 id 与内容）。 */
interface UndoEntry {
  undo: () => Promise<boolean>
  redo: () => Promise<boolean>
}
const undoStack: UndoEntry[] = []
const redoStack: UndoEntry[] = []
const UNDO_LIMIT = 50

const isActive = (s: string | undefined): boolean => s === 'pending' || s === 'running'

export const useMindmapStore = create<MindState>((set, get) => {
  const applyTree = (t: MindTree): void => {
    const prev = get().trees[t.view]
    const prevCollapsed = get().collapsed[t.view]
    const collapsed = prev && prevCollapsed ? mergeCollapsed(prevCollapsed, new Set(prev.nodes.map((n) => n.id)), t.nodes) : initialCollapsed(t.nodes)
    set((s) => ({ trees: { ...s.trees, [t.view]: t }, collapsed: { ...s.collapsed, [t.view]: collapsed } }))
  }

  const stopPolling = (): void => {
    window.clearTimeout(pollTimer)
    pollTimer = undefined
  }

  /** 有任务在跑时每 1.5 秒同步一次状态与进度；全部结束后自动停止。 */
  const ensurePolling = (): void => {
    if (pollTimer !== undefined) return
    const tick = async (): Promise<void> => {
      const paperId = get().paperId
      if (!paperId) return stopPolling()
      try {
        const ov = await mindmapApi.overview(paperId)
        if (get().paperId !== paperId) return stopPolling()
        set({ overview: ov })
        const progress: Partial<Record<MindView, JobProgress>> = {}
        for (const v of MIND_VIEWS) {
          const st = ov.views[v]
          const before = get().trees[v]?.status
          if (isActive(st.status)) {
            if (st.job_id) {
              try {
                const j = await api.jobs.get(st.job_id)
                progress[v] = { progress: j.progress, message: j.message }
              } catch {
                /* 下一轮再取 */
              }
            }
            if (!isActive(before)) await get().reload(v)
          } else if (isActive(before) || (st.status === 'ready' && st.count !== (get().trees[v]?.nodes.length ?? 0))) {
            await get().reload(v)
            const t = get().trees[v]
            if (isActive(before) && t) {
              if (t.status === 'failed') toast.error(`${t.view_label}思维导图没有生成成功`, t.error ?? undefined)
              else if (t.status === 'ready') {
                const m = t.verification?.merge
                toast.success(`${t.view_label}思维导图已生成`, m && m.kept_user > 0 ? `保留了你编辑过的 ${m.kept_user} 个节点` : `${t.nodes.length} 个节点`)
              }
            }
          }
        }
        set({ progress })
        pollTimer = undefined
        if (MIND_VIEWS.some((v) => isActive(ov.views[v].status))) {
          pollTimer = window.setTimeout(() => void tick(), 1500)
          return
        }
        if (get().selectedId) void get().select(get().selectedId, { focus: false })
      } catch {
        pollTimer = window.setTimeout(() => void tick(), 3000)
      }
    }
    pollTimer = window.setTimeout(() => void tick(), 600)
  }

  const syncUndo = (): void => set({ canUndo: undoStack.length > 0, canRedo: redoStack.length > 0 })
  const pushUndo = (e: UndoEntry): void => {
    undoStack.push(e)
    if (undoStack.length > UNDO_LIMIT) undoStack.shift()
    redoStack.length = 0
    syncUndo()
  }
  const clearUndo = (): void => {
    undoStack.length = 0
    redoStack.length = 0
    syncUndo()
  }

  /** 删掉节点（含子树）并返回快照；撤销/重做都走这里，快照原样交给 restore 即可恢复同一个 id 与内容。 */
  const removeForUndo = async (id: string): Promise<NodeSnapshot | null> => {
    try {
      const r = await mindmapApi.node.remove(id)
      await get().reload()
      const sel = get().selectedId
      if (sel === id || !get().trees[get().view]?.nodes.some((n) => n.id === sel)) await get().select(r.parent_id, { focus: true })
      return r.snapshot
    } catch (err) {
      toast.error('撤销失败', errorMessage(err))
      return null
    }
  }
  const restoreForUndo = async (snapshot: NodeSnapshot, selectId: string): Promise<boolean> => {
    try {
      await mindmapApi.node.restore(snapshot)
      await get().reload()
      await get().select(selectId, { focus: true })
      return true
    } catch (err) {
      toast.error('恢复失败', errorMessage(err))
      return false
    }
  }
  /** 新增节点的撤销 = 删掉它；重做 = 用删除时的快照原样恢复。 */
  const pushAddUndo = (id: string): void => {
    let snap: NodeSnapshot | null = null
    pushUndo({
      undo: async () => {
        snap = await removeForUndo(id)
        return snap !== null
      },
      redo: async () => (snap ? restoreForUndo(snap, id) : false)
    })
  }
  /** 删除节点的撤销 = 用快照恢复；重做 = 再删一次（再取一份快照）。 */
  const pushDeleteUndo = (id: string, snapshot: NodeSnapshot): void => {
    let snap = snapshot
    pushUndo({
      undo: () => restoreForUndo(snap, id),
      redo: async () => {
        const next = await removeForUndo(id)
        if (next) snap = next
        return next !== null
      }
    })
  }

  return {
    paperId: null,
    view: 'logic',
    trees: {},
    overview: null,
    loading: false,
    error: null,
    collapsed: {},
    selectedId: null,
    detail: null,
    focus: null,
    query: '',
    hits: [],
    kindFilter: new Set(),
    progress: {},
    explain: {},
    editRequest: null,
    canUndo: false,
    canRedo: false,

    open: async (paperId, opts = {}) => {
      if (get().paperId !== paperId) {
        stopPolling()
        explainCtl?.abort()
        clearUndo()
        set({
          paperId, trees: {}, overview: null, collapsed: {}, selectedId: null, detail: null, focus: null, query: '', hits: [],
          kindFilter: new Set(), progress: {}, explain: {}, error: null, view: 'logic'
        })
      } else {
        set({ view: 'logic' })
      }
      set({ loading: true, error: null })
      try {
        const ov = await mindmapApi.overview(paperId)
        if (get().paperId !== paperId) return
        set({ overview: ov })
        // 思维导图只有一个视角：梳理文章的逻辑框架（旧版本生成过的方法、实验视角的数据还在，只是不再显示）
        const view: MindView = 'logic'
        set({ view })
        await get().reload(view)
        if (opts.nodeId) await get().select(opts.nodeId, { focus: true })
        if (MIND_VIEWS.some((v) => isActive(ov.views[v].status))) ensurePolling()
      } catch (err) {
        if (get().paperId === paperId) set({ error: errorMessage(err) })
      } finally {
        if (get().paperId === paperId) set({ loading: false })
      }
    },

    reset: () => {
      stopPolling()
      explainCtl?.abort()
      clearUndo()
      set({ paperId: null, trees: {}, overview: null, collapsed: {}, selectedId: null, detail: null, focus: null, query: '', hits: [], progress: {}, explain: {}, error: null })
    },

    setView: async (view) => {
      set({ view, selectedId: null, detail: null, kindFilter: new Set() })
      if (!get().trees[view]) await get().reload(view)
      if (isActive(get().trees[view]?.status)) ensurePolling()
    },

    reload: async (view) => {
      const paperId = get().paperId
      const v = view ?? get().view
      if (!paperId) return
      try {
        const t = await mindmapApi.get(paperId, v)
        if (get().paperId === paperId) {
          applyTree(t)
          if (isActive(t.status)) ensurePolling()
        }
      } catch (err) {
        if (get().paperId === paperId) set({ error: errorMessage(err) })
      }
    },

    select: async (id, opts = {}) => {
      const seq = ++detailSeq
      if (id === null) {
        set({ selectedId: null, detail: null })
        return
      }
      const view = get().view
      const t = get().trees[view]
      if (t && opts.focus) {
        set((s) => ({ collapsed: { ...s.collapsed, [view]: expandTo(s.collapsed[view] ?? new Set(), t.nodes, id) }, focus: { id, seq: ++focusSeq } }))
      }
      set({ selectedId: id })
      try {
        const d = await mindmapApi.node.get(id)
        if (seq === detailSeq && get().selectedId === id) set({ detail: d })
      } catch {
        if (seq === detailSeq) set({ detail: null })
      }
    },

    toggleCollapse: (id) => {
      const view = get().view
      const cur = new Set(get().collapsed[view] ?? [])
      const now = !cur.has(id)
      if (now) cur.add(id)
      else cur.delete(id)
      set((s) => ({ collapsed: { ...s.collapsed, [view]: cur } }))
      const paperId = get().paperId
      if (paperId) void mindmapApi.node.patch(id, { collapsed: now }).catch(() => undefined)
    },

    setAllCollapsed: (collapsed) => {
      const { paperId, view } = get()
      const t = get().trees[view]
      if (!paperId || !t) return
      const ids = new Set(t.nodes.filter((n) => n.child_count > 0 && n.depth >= 2).map((n) => n.id))
      set((s) => ({ collapsed: { ...s.collapsed, [view]: collapsed ? ids : new Set() } }))
      void mindmapApi.collapseAll(paperId, view, collapsed, 2).catch(() => undefined)
    },

    generate: async (view, branchId) => {
      const paperId = get().paperId
      if (!paperId) return false
      try {
        const r = await mindmapApi.generate(paperId, view, branchId)
        Object.values(r.jobs).forEach((id) => useJobsStore.getState().track(id))
        await get().reload(view === 'all' ? get().view : view)
        if (view === 'all') await Promise.all(MIND_VIEWS.filter((v) => v !== get().view).map((v) => get().reload(v)))
        ensurePolling()
        return true
      } catch (err) {
        toast.error('无法开始生成', errorMessage(err))
        return false
      }
    },

    patchNode: async (id, patch, opts = {}) => {
      const view = get().view
      const t = get().trees[view]
      if (!t) return false
      const before = t.nodes.find((n) => n.id === id)
      if (!before) return false
      const visual = 'x' in patch || 'y' in patch || 'collapsed' in patch
      // 乐观更新
      const local: Partial<MindNode> = {}
      if (patch.title !== undefined) local.title = patch.title.trim()
      if (patch.summary !== undefined) local.summary = patch.summary.trim()
      if (patch.kind !== undefined) local.kind = patch.kind
      if (!visual || patch.title !== undefined) local.user_edited = true
      set((s) => ({ trees: { ...s.trees, [view]: { ...t, nodes: patchLocal(t.nodes, id, local) } } }))
      try {
        const d = await mindmapApi.node.patch(id, patch)
        set((s) => {
          const cur = s.trees[view]
          if (!cur) return s
          return {
            trees: { ...s.trees, [view]: { ...cur, nodes: patchLocal(cur.nodes, id, { title: d.title, summary: d.summary, kind: d.kind, kind_label: d.kind_label, user_edited: d.user_edited }) } },
            detail: s.selectedId === id ? d : s.detail
          }
        })
        if (patch.parent_id !== undefined) await get().reload(view)
        const keys = (['title', 'summary', 'kind'] as const).filter((k) => patch[k] !== undefined)
        if (opts.record !== false && keys.length > 0) {
          const prev: MindNodePatch = {}
          const next: MindNodePatch = {}
          for (const k of keys) {
            prev[k] = before[k]
            next[k] = patch[k]
          }
          pushUndo({
            undo: () => get().patchNode(id, prev, { record: false }),
            redo: () => get().patchNode(id, next, { record: false })
          })
        }
        return true
      } catch (err) {
        set((s) => ({ trees: { ...s.trees, [view]: { ...(s.trees[view] as MindTree), nodes: patchLocal((s.trees[view] as MindTree).nodes, id, before) } } }))
        toast.error('保存修改失败', errorMessage(err))
        return false
      }
    },

    moveNode: async (id, parentId, index) => {
      const view = get().view
      const t = get().trees[view]
      const node = t?.nodes.find((n) => n.id === id)
      if (!t || !node || !node.parent_id) return false
      const oldParent = node.parent_id
      const oldIndex = indexInParent(t.nodes, id)
      const ok = await get().patchNode(id, { parent_id: parentId, index }, { record: false })
      if (!ok) return false
      // 新父节点要展开，才能看到刚移进来的节点
      set((s) => ({ collapsed: { ...s.collapsed, [view]: new Set([...(s.collapsed[view] ?? [])].filter((x) => x !== parentId)) } }))
      void mindmapApi.node.patch(parentId, { collapsed: false }).catch(() => undefined)
      pushUndo({
        undo: () => get().patchNode(id, { parent_id: oldParent, index: oldIndex }, { record: false }),
        redo: () => get().patchNode(id, { parent_id: parentId, index }, { record: false })
      })
      await get().select(id, { focus: false })
      return true
    },

    quickAdd: async (mode, id) => {
      const d = mode === 'child' ? await get().addChild(id, '新节点') : await get().addSibling(id, '新节点')
      if (d) get().requestEdit(d.id)
    },

    requestEdit: (id) => set({ editRequest: { id, seq: ++editSeq } }),

    undo: async () => {
      const e = undoStack.pop()
      syncUndo()
      if (!e) return
      if (await e.undo()) redoStack.push(e)
      else toast.error('无法撤销', '这个修改涉及的节点已经不存在或已变化。')
      syncUndo()
    },

    redo: async () => {
      const e = redoStack.pop()
      syncUndo()
      if (!e) return
      if (await e.redo()) undoStack.push(e)
      else toast.error('无法重做', '这个修改涉及的节点已经不存在或已变化。')
      syncUndo()
    },

    collapseToDepth: (depth) => {
      const { paperId, view } = get()
      const t = get().trees[view]
      if (!paperId || !t) return
      set((s) => ({ collapsed: { ...s.collapsed, [view]: collapsedForDepth(t.nodes, depth) } }))
      void mindmapApi
        .collapseAll(paperId, view, false, 1)
        .then(() => (Number.isFinite(depth) ? mindmapApi.collapseAll(paperId, view, true, depth) : undefined))
        .catch(() => undefined)
    },

    addChild: async (parentId, title) => {
      try {
        const d = await mindmapApi.node.addChild(parentId, { title })
        set((s) => ({ collapsed: { ...s.collapsed, [s.view]: new Set([...(s.collapsed[s.view] ?? [])].filter((x) => x !== parentId)) } }))
        await get().reload()
        await get().select(d.id, { focus: true })
        pushAddUndo(d.id)
        return d
      } catch (err) {
        toast.error('添加节点失败', errorMessage(err))
        return null
      }
    },

    addSibling: async (id, title) => {
      try {
        const d = await mindmapApi.node.addSibling(id, { title })
        await get().reload()
        await get().select(d.id, { focus: true })
        pushAddUndo(d.id)
        return d
      } catch (err) {
        toast.error('添加节点失败', errorMessage(err))
        return null
      }
    },

    deleteNode: async (id) => {
      const view = get().view
      const t = get().trees[view]
      try {
        const r = await mindmapApi.node.remove(id)
        if (t) set((s) => ({ trees: { ...s.trees, [view]: { ...t, nodes: removeBranch(t.nodes, id) } } }))
        // 选中节点被删掉时，选中它的父节点
        if (get().selectedId === id || !get().trees[view]?.nodes.some((n) => n.id === get().selectedId)) await get().select(r.parent_id, { focus: true })
        await get().reload(view)
        pushDeleteUndo(id, r.snapshot)
        return true
      } catch (err) {
        toast.error('删除失败', errorMessage(err))
        return false
      }
    },

    commitPositions: async (items) => {
      const { paperId, view } = get()
      const t = get().trees[view]
      if (!paperId || !t || items.length === 0) return
      const map = new Map(items.map((i) => [i.id, i]))
      set((s) => ({ trees: { ...s.trees, [view]: { ...t, nodes: t.nodes.map((n) => (map.has(n.id) ? { ...n, x: map.get(n.id)!.x, y: map.get(n.id)!.y } : n)) } } }))
      try {
        await mindmapApi.savePositions(paperId, view, items)
      } catch {
        /* 位置没保存成功只影响下次打开的布局，不打扰用户 */
      }
    },

    resetLayout: async () => {
      const { paperId, view } = get()
      const t = get().trees[view]
      if (!paperId || !t) return
      set((s) => ({ trees: { ...s.trees, [view]: { ...t, nodes: t.nodes.map((n) => ({ ...n, x: null, y: null })) } } }))
      try {
        await mindmapApi.resetPositions(paperId, view)
      } catch (err) {
        toast.error('重置布局失败', errorMessage(err))
      }
    },

    runSearch: async (q) => {
      const paperId = get().paperId
      set({ query: q })
      const text = q.trim()
      if (!paperId || !text) {
        set({ hits: [] })
        return
      }
      const seq = ++searchSeq
      try {
        const r = await mindmapApi.search(paperId, text)
        if (seq === searchSeq) set({ hits: r.items })
      } catch {
        if (seq === searchSeq) set({ hits: [] })
      }
    },

    toggleKind: (kind) =>
      set((s) => {
        const next = new Set(s.kindFilter)
        if (next.has(kind)) next.delete(kind)
        else next.add(kind)
        return { kindFilter: next }
      }),
    clearKinds: () => set({ kindFilter: new Set() }),

    exportView: async (format) => {
      const { paperId, view } = get()
      if (!paperId) return
      try {
        const { blob, filename } = await mindmapApi.export(paperId, view, format)
        downloadText(filename, await blob.text(), blob.type || (format === 'json' ? 'application/json' : 'text/markdown'))
        toast.success('已导出', filename)
      } catch (err) {
        toast.error('导出失败', errorMessage(err))
      }
    },

    explainNode: async (id, question) => {
      explainCtl?.abort()
      const ctl = new AbortController()
      explainCtl = ctl
      const base: ExplainState = { nodeId: id, status: 'streaming', text: '', card: null, error: null }
      set((s) => ({ explain: { ...s.explain, [id]: base } }))
      const update = (patch: Partial<ExplainState>): void =>
        set((s) => ({ explain: { ...s.explain, [id]: { ...(s.explain[id] ?? base), ...patch } } }))
      try {
        await mindmapApi.node.explain(
          id,
          question,
          (event, data) => {
            const d = data as Record<string, unknown>
            if (event === 'delta') update({ text: (get().explain[id]?.text ?? '') + String(d.text ?? '') })
            else if (event === 'done') update({ status: 'done', card: d.card as InsightCard })
            else if (event === 'error') update({ status: 'error', error: String(d.message ?? '解释失败') })
          },
          ctl.signal
        )
        if (get().explain[id]?.status === 'streaming') update({ status: 'error', error: '连接中断，没有收到完整的回答。' })
        if (get().explain[id]?.status === 'done' && get().selectedId === id) void get().select(id, { focus: false })
      } catch (err) {
        if (ctl.signal.aborted) update({ status: 'error', error: '已停止。' })
        else update({ status: 'error', error: errorMessage(err) })
      }
    },

    stopExplain: () => explainCtl?.abort()
  }
})

/** 当前视角的树（可能尚未加载）。 */
export function selectTree(s: Pick<MindState, 'trees' | 'view'>): MindTree | undefined {
  return s.trees[s.view]
}
