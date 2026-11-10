import { create } from 'zustand'
import { errorMessage } from '@/lib/api'
import { referencesApi } from '@/lib/api.references'
import type { CitationMarker, RefContext, RefItem, RefResolution, SummarizeDone } from '@/lib/types.references'
import type { ParaRefs } from './refModel'
import { MAX_TRANSLATE_CHARS } from '@/components/selectTranslateModel'
import { friendlyTranslateError, streamTranslate } from '@/lib/translateText'

export interface Rect {
  left: number
  right: number
  top: number
  bottom: number
}

export interface ResState {
  status: 'loading' | 'ready' | 'error'
  data?: RefResolution
  error?: string
}

export interface SumState {
  status: 'streaming' | 'done' | 'error'
  text: string
  stage?: string
  done?: SummarizeDone
  notes: string[]
  error?: string
}

export interface TrState {
  status: 'streaming' | 'done' | 'error'
  text: string
  model?: string
  cached?: boolean
  error?: string
}

interface RefState {
  /** 摘要的中文译文（按参考文献编号）：同一篇的摘要只翻译一次，再次打开直接显示 */
  abstractTr: Record<number, TrState>
  translateAbstract: (index: number, force?: boolean) => Promise<void>
  paperId: string | null
  status: 'idle' | 'loading' | 'ready' | 'error'
  style: string
  refs: RefItem[]
  byIndex: ReadonlyMap<number, RefItem>
  /** 参考文献列表里那一条的段落序号 → 编号（列表里的条目也能点击/右键） */
  ordToIndex: ReadonlyMap<number, number>
  valid: ReadonlySet<number>
  paraRefs: ReadonlyMap<number, ParaRefs>

  popover: { refs: number[]; pos: number; rect: Rect } | null
  /** 鼠标停在引用角标上时的小卡片：显示这几条参考文献的标题；点角标才是完整浮层 */
  hover: { refs: number[]; rect: Rect } | null
  menu: { refs: number[]; pos: number; x: number; y: number } | null
  drawer: number | null
  resolutions: Record<number, ResState>
  summaries: Record<number, SumState>
  contexts: Record<number, RefContext[]>

  load: (paperId: string) => Promise<void>
  reset: () => void
  openPopover: (refs: number[], rect: Rect) => void
  setHover: (h: { refs: number[]; rect: Rect } | null) => void
  /** 悬停卡片的显示和收起都稍有延迟：鼠标可以从角标移进卡片而不会闪掉 */
  showHover: (h: { refs: number[]; rect: Rect }) => void
  hideHover: () => void
  keepHover: () => void
  stepPopover: (delta: number) => void
  openMenu: (refs: number[], x: number, y: number) => void
  openDrawer: (index: number) => void
  closeAll: () => void
  resolve: (index: number, force?: boolean) => Promise<void>
  /** 加入文库之后刷新“是否已在文库里”（读后端缓存的查询结果，不联网重查）：抽屉里的按钮据此变成“在文库中打开” */
  refreshInLibrary: (index: number) => Promise<void>
  loadContexts: (index: number) => Promise<void>
  summarize: (index: number) => Promise<void>
}

const empty = {
  abstractTr: {} as Record<number, TrState>,
  paperId: null,
  status: 'idle' as const,
  style: 'unknown',
  refs: [] as RefItem[],
  byIndex: new Map<number, RefItem>(),
  ordToIndex: new Map<number, number>(),
  valid: new Set<number>(),
  paraRefs: new Map<number, ParaRefs>(),
  popover: null,
  hover: null,
  menu: null,
  drawer: null,
  resolutions: {} as Record<number, ResState>,
  summaries: {} as Record<number, SumState>,
  contexts: {} as Record<number, RefContext[]>
}

let hoverTimer: number | null = null
function clearHoverTimer(): void {
  if (hoverTimer !== null) window.clearTimeout(hoverTimer)
  hoverTimer = null
}

const controllers = new Map<number, AbortController>()
const trControllers = new Map<number, AbortController>()

export const useRefStore = create<RefState>((set, get) => ({
  ...empty,

  translateAbstract: async (index, force = false) => {
    const paperId = get().paperId
    const abstract = get().resolutions[index]?.data?.abstract
    if (!paperId || !abstract) return // 没有可信摘要（含“与标题不相符已忽略”）就没有可翻译的内容
    const cur = get().abstractTr[index]
    if (!force && cur && (cur.status === 'done' || cur.status === 'streaming')) return
    trControllers.get(index)?.abort()
    const ctrl = new AbortController()
    trControllers.set(index, ctrl)
    const put = (patch: Partial<TrState>): void =>
      set((s) => ({ abstractTr: { ...s.abstractTr, [index]: { ...(s.abstractTr[index] ?? { text: '' }), status: 'streaming', ...patch } as TrState } }))
    set((s) => ({ abstractTr: { ...s.abstractTr, [index]: { status: 'streaming', text: '' } } }))
    await streamTranslate(
      paperId,
      abstract.slice(0, MAX_TRANSLATE_CHARS),
      {
        onMeta: (m) => put({ model: m.model, cached: m.cached }),
        onDelta: (d) => put({ text: (get().abstractTr[index]?.text ?? '') + d }),
        onDone: (r) => put({ status: 'done', text: r.translation, model: r.model, cached: r.cached }),
        onError: (e) => put({ status: 'error', error: friendlyTranslateError(e) })
      },
      ctrl.signal,
      force
    )
  },

  load: async (paperId) => {
    if (get().paperId === paperId && get().status !== 'error') return
    controllers.forEach((c) => c.abort())
    controllers.clear()
    trControllers.forEach((c) => c.abort())
    trControllers.clear()
    set({ ...empty, paperId, status: 'loading' })
    try {
      const [refs, cits] = await Promise.all([referencesApi.list(paperId), referencesApi.citations(paperId)])
      if (get().paperId !== paperId) return
      const byIndex = new Map(refs.items.map((r) => [r.index, r]))
      const ordToIndex = new Map<number, number>()
      for (const r of refs.items) for (const o of r.ords) ordToIndex.set(o, r.index)
      const paraRefs = new Map<number, ParaRefs>()
      for (const it of cits.items) {
        const sup = new Set<number>()
        const text: CitationMarker[] = []
        for (const m of it.markers) {
          if (m.kind === 'sup') m.refs.forEach((n) => sup.add(n))
          else text.push(m)
        }
        paraRefs.set(it.ord, { sup, text })
      }
      set({ status: 'ready', style: refs.style, refs: refs.items, byIndex, ordToIndex, valid: new Set(byIndex.keys()), paraRefs })
    } catch {
      // 参考文献功能读不到不影响阅读：正文里不会出现可点击的引用标记
      if (get().paperId === paperId) set({ status: 'error' })
    }
  },

  reset: () => {
    controllers.forEach((c) => c.abort())
    controllers.clear()
    trControllers.forEach((c) => c.abort())
    trControllers.clear()
    set({ ...empty })
  },

  openPopover: (refs, rect) => set({ popover: { refs, pos: 0, rect }, menu: null, hover: null }),
  setHover: (hover) => set((s) => (s.popover || s.menu || s.drawer !== null ? { hover: null } : { hover })),
  showHover: (h) => {
    clearHoverTimer()
    hoverTimer = window.setTimeout(() => get().setHover(h), 180)
  },
  hideHover: () => {
    clearHoverTimer()
    hoverTimer = window.setTimeout(() => set({ hover: null }), 220)
  },
  keepHover: () => clearHoverTimer(),
  stepPopover: (delta) =>
    set((s) => (s.popover ? { popover: { ...s.popover, pos: Math.min(Math.max(s.popover.pos + delta, 0), s.popover.refs.length - 1) } } : s)),
  openMenu: (refs, x, y) => set({ menu: { refs, pos: 0, x, y }, popover: null, hover: null }),
  openDrawer: (index) => {
    set({ drawer: index, popover: null, menu: null, hover: null })
    void get().resolve(index)
    void get().loadContexts(index)
  },
  closeAll: () => set({ popover: null, menu: null, hover: null }),

  resolve: async (index, force = false) => {
    const paperId = get().paperId
    if (!paperId) return
    const cur = get().resolutions[index]
    if (!force && cur && (cur.status === 'ready' || cur.status === 'loading')) return
    set((s) => ({ resolutions: { ...s.resolutions, [index]: { status: 'loading' } } }))
    try {
      const data = await referencesApi.resolve(paperId, index, force)
      if (get().paperId !== paperId) return
      set((s) => ({ resolutions: { ...s.resolutions, [index]: { status: 'ready', data } } }))
    } catch (err) {
      if (get().paperId === paperId) set((s) => ({ resolutions: { ...s.resolutions, [index]: { status: 'error', error: errorMessage(err) } } }))
    }
  },

  refreshInLibrary: async (index) => {
    const paperId = get().paperId
    const cur = get().resolutions[index]
    if (!paperId || !cur || cur.status !== 'ready') return
    try {
      const data = await referencesApi.resolve(paperId, index, false)
      if (get().paperId === paperId) set((s) => ({ resolutions: { ...s.resolutions, [index]: { status: 'ready', data } } }))
    } catch {
      /* 刷新不了就保持原样 */
    }
  },

  loadContexts: async (index) => {
    const paperId = get().paperId
    if (!paperId || get().contexts[index]) return
    try {
      const { items } = await referencesApi.contexts(paperId, index)
      if (get().paperId === paperId) set((s) => ({ contexts: { ...s.contexts, [index]: items } }))
    } catch {
      /* 引用处读不到不影响其它内容 */
    }
  },

  summarize: async (index) => {
    const paperId = get().paperId
    if (!paperId) return
    controllers.get(index)?.abort()
    const ctrl = new AbortController()
    controllers.set(index, ctrl)
    const put = (patch: Partial<SumState>): void =>
      set((s) => {
        const prev: SumState = s.summaries[index] ?? { status: 'streaming', text: '', notes: [] }
        return { summaries: { ...s.summaries, [index]: { ...prev, status: 'streaming', ...patch } } }
      })
    set((s) => ({ summaries: { ...s.summaries, [index]: { status: 'streaming', text: '', notes: [] } } }))
    try {
      await referencesApi.summarize(
        paperId,
        index,
        (event, data) => {
          const d = (data ?? {}) as Record<string, unknown>
          if (event === 'meta') put({ notes: (d.notes as string[] | undefined) ?? [] })
          else if (event === 'stage') put({ stage: String(d.message ?? '') })
          else if (event === 'delta') put({ text: (get().summaries[index]?.text ?? '') + String(d.text ?? '') })
          else if (event === 'done') {
            const done = d as unknown as SummarizeDone
            put({ status: 'done', done, text: done.summary ?? '', notes: done.notes ?? get().summaries[index]?.notes ?? [], stage: undefined })
          } else if (event === 'error') put({ status: 'error', error: String(d.message ?? '概括失败，请重试。') })
        },
        ctrl.signal
      )
      if (get().summaries[index]?.status === 'streaming') put({ status: 'done' })
    } catch (err) {
      if (ctrl.signal.aborted) return
      put({ status: 'error', error: errorMessage(err) })
    }
  }
}))
