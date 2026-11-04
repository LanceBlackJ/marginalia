import { create } from 'zustand'
import { errorMessage } from '@/lib/api'
import { codeApi } from '@/lib/api.code'
import { toast } from '@/components/ui'
import type { Job } from '@/lib/types'
import type {
  CodeCard,
  CodeFileEntry,
  CodeRepo,
  CodeSource,
  CodeSymbol,
  ConsistencyState,
  ExplainMeta,
  ExplainTarget,
  FileData,
  Flow,
  PaperCodeLink,
  PaperModule,
  SymbolGraph,
  SymbolPaper
} from '@/lib/types.code'

export type CodeTab = 'code' | 'flow' | 'mapping' | 'overview' | 'consistency' | 'repro'
export type SideTab = 'explain' | 'calls' | 'paper'

export interface Highlight {
  path: string
  a: number
  b: number
  nonce: number
}

export interface ExplainRun {
  id: number
  target: ExplainTarget
  label: string
  question: string
  text: string
  status: 'streaming' | 'failed' | 'stopped'
  meta: ExplainMeta | null
  escalatedTo: string | null
  error: { code: string; message: string } | null
}

interface CodeState {
  paperId: string | null
  loading: boolean
  loadError: string | null
  sources: CodeSource[]
  repos: CodeRepo[]
  repoId: string | null
  tab: CodeTab
  sideTab: SideTab

  files: CodeFileEntry[]
  openPath: string | null
  file: FileData | null
  fileLoading: boolean
  fileError: string | null
  selectedSymbol: CodeSymbol | null
  highlight: Highlight | null
  /** 用户在代码里选中的行（行号点击或鼠标选择） */
  lineSel: { path: string; a: number; b: number } | null
  graph: SymbolGraph | null
  symbolPaper: SymbolPaper | null

  cards: CodeCard[]
  explainRun: ExplainRun | null

  flow: Flow | null
  flowLoading: boolean
  flowError: string | null
  flowRoot: number | null
  flowDepth: number
  flowSelected: number | null
  annotating: boolean

  modules: PaperModule[]
  links: PaperCodeLink[]
  mappingLoaded: boolean
  associating: boolean

  consistency: ConsistencyState | null
  checking: boolean

  init: (paperId: string) => Promise<void>
  reset: () => void
  refreshRepos: (selectId?: string) => Promise<void>
  selectRepo: (id: string | null) => Promise<void>
  removeRepo: (id: string) => Promise<void>
  reanalyze: () => Promise<void>
  reanalyzing: boolean
  setLineSel: (sel: { path: string; a: number; b: number } | null) => void
  setTab: (t: CodeTab) => void
  setSideTab: (t: SideTab) => void

  openFile: (path: string, range?: { a: number; b: number }) => Promise<void>
  selectSymbol: (s: CodeSymbol) => Promise<void>
  goToSymbolId: (symbolId: number) => Promise<void>
  goToLines: (path: string, a: number, b: number) => Promise<void>

  explain: (target: ExplainTarget, label: string, question?: string) => Promise<void>
  stopExplain: () => void
  dismissExplain: () => void
  removeCard: (id: string) => Promise<void>

  loadFlow: (rootId?: number | null, depth?: number) => Promise<void>
  selectFlowNode: (id: number | null) => void
  annotateFlow: () => Promise<void>

  loadMapping: () => Promise<void>
  /** auto：打开映射页时没有任何关联，自动做一次本地词法匹配（不用 AI、不发送内容、不弹提示）；手动点“生成关联”才会用 AI。 */
  associate: (opts?: { auto?: boolean }) => Promise<void>
  setLinkStatus: (id: string, status: PaperCodeLink['status']) => Promise<void>
  loadConsistency: () => Promise<void>
  runConsistency: () => Promise<void>
}

let explainCtrl: AbortController | null = null
let explainSeq = 0
let repoPoll: number | null = null
let alive = 0 // 页面切换/重置后，丢弃过期的异步结果

/** 轮询任务直到结束。 */
export async function watchJob(jobId: string, onUpdate?: (j: Job) => void, intervalMs = 1000): Promise<Job> {
  for (;;) {
    const j = await codeApi.job(jobId)
    onUpdate?.(j)
    if (['completed', 'failed', 'cancelled'].includes(j.status)) return j
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

const emptyRepoState = {
  files: [] as CodeFileEntry[],
  openPath: null,
  file: null,
  fileError: null,
  selectedSymbol: null,
  highlight: null,
  lineSel: null,
  graph: null,
  symbolPaper: null,
  cards: [] as CodeCard[],
  explainRun: null,
  flow: null,
  flowError: null,
  flowRoot: null,
  flowSelected: null,
  modules: [] as PaperModule[],
  links: [] as PaperCodeLink[],
  mappingLoaded: false,
  consistency: null
}

export const useCodeStore = create<CodeState>((set, get) => {
  const stopRepoPoll = (): void => {
    if (repoPoll !== null) window.clearInterval(repoPoll)
    repoPoll = null
  }

  const loadRepoData = async (repoId: string, token: number): Promise<void> => {
    try {
      const [tree, cards] = await Promise.all([codeApi.tree(repoId), codeApi.cards(repoId)])
      if (token !== alive || get().repoId !== repoId) return
      set({ files: tree.files, cards: cards.items })
    } catch (err) {
      if (token === alive) toast.error('加载代码仓库失败', errorMessage(err))
    }
  }

  return {
    paperId: null,
    loading: false,
    loadError: null,
    sources: [],
    repos: [],
    repoId: null,
    tab: 'code',
    sideTab: 'explain',
    ...emptyRepoState,
    fileLoading: false,
    flowLoading: false,
    flowDepth: 4,
    annotating: false,
    associating: false,
    checking: false,
    reanalyzing: false,

    init: async (paperId) => {
      alive += 1
      const token = alive
      stopRepoPoll()
      set({ paperId, loading: true, loadError: null, sources: [], repos: [], repoId: null, tab: 'code', ...emptyRepoState })
      try {
        const [sources, repos] = await Promise.all([codeApi.sources(paperId), codeApi.repos(paperId)])
        if (token !== alive) return
        set({ sources: sources.items, repos: repos.items, loading: false })
        const first = repos.items.find((r) => r.status === 'ready') ?? repos.items[0]
        if (first) await get().selectRepo(first.id)
      } catch (err) {
        if (token === alive) set({ loading: false, loadError: errorMessage(err) })
      }
    },

    reset: () => {
      alive += 1
      stopRepoPoll()
      explainCtrl?.abort()
      explainCtrl = null
      set({ paperId: null, repos: [], repoId: null, sources: [], loading: false, loadError: null, tab: 'code', ...emptyRepoState })
    },

    refreshRepos: async (selectId) => {
      const { paperId } = get()
      if (!paperId) return
      const repos = await codeApi.repos(paperId)
      set({ repos: repos.items })
      if (selectId) await get().selectRepo(selectId)
    },

    selectRepo: async (id) => {
      stopRepoPoll()
      explainCtrl?.abort()
      const token = alive
      set({ repoId: id, ...emptyRepoState })
      if (!id) return
      const repo = get().repos.find((r) => r.id === id)
      if (repo && repo.status === 'ready') {
        await loadRepoData(id, token)
        return
      }
      // 还在下载/分析：轮询到就绪或失败
      repoPoll = window.setInterval(async () => {
        try {
          const r = await codeApi.repo(id)
          if (token !== alive) return stopRepoPoll()
          set({ repos: get().repos.map((x) => (x.id === id ? r : x)) })
          if (r.status === 'ready') {
            stopRepoPoll()
            await loadRepoData(id, token)
          } else if (r.status === 'failed') stopRepoPoll()
        } catch {
          stopRepoPoll()
        }
      }, 1500)
    },

    removeRepo: async (id) => {
      await codeApi.removeRepo(id)
      const repos = get().repos.filter((r) => r.id !== id)
      set({ repos })
      if (get().repoId === id) await get().selectRepo(repos[0]?.id ?? null)
    },

    reanalyze: async () => {
      const { repoId } = get()
      if (!repoId) return
      set({ reanalyzing: true })
      try {
        const { job_id } = await codeApi.reanalyze(repoId)
        const job = await watchJob(job_id)
        if (job.status !== 'completed') throw new Error(job.error ?? '任务未完成')
        await get().refreshRepos()
        await get().selectRepo(repoId)
        toast.success('已重新分析代码', '没有重新下载；符号、调用关系和参数已按最新解析器更新。')
      } catch (err) {
        toast.error('重新分析失败', errorMessage(err))
      } finally {
        set({ reanalyzing: false })
      }
    },

    setLineSel: (sel) => set({ lineSel: sel }),
    setTab: (t) => set({ tab: t }),
    setSideTab: (t) => set({ sideTab: t }),

    openFile: async (path, range) => {
      const { repoId, openPath, file } = get()
      if (!repoId || !path) return
      set({ tab: 'code' })
      const nonce = Date.now()
      if (openPath === path && file) {
        set({ highlight: range ? { path, a: range.a, b: range.b, nonce } : null })
        return
      }
      set({ openPath: path, fileLoading: true, fileError: null, selectedSymbol: null, graph: null, symbolPaper: null, highlight: null, lineSel: null })
      try {
        const f = await codeApi.file(repoId, path)
        if (get().repoId !== repoId || get().openPath !== path) return
        set({ file: f, fileLoading: false, highlight: range ? { path, a: range.a, b: range.b, nonce } : null })
      } catch (err) {
        if (get().openPath === path) set({ file: null, fileLoading: false, fileError: errorMessage(err) })
      }
    },

    selectSymbol: async (s) => {
      const { repoId } = get()
      if (!repoId) return
      if (get().openPath !== s.path) await get().openFile(s.path)
      set({ selectedSymbol: s, highlight: { path: s.path, a: s.line_start, b: s.line_end, nonce: Date.now() }, graph: null, symbolPaper: null })
      try {
        const [graph, paper] = await Promise.all([codeApi.graph(repoId, s.id), codeApi.symbolPaper(s.id).catch(() => null)])
        if (get().selectedSymbol?.id === s.id) set({ graph, symbolPaper: paper })
      } catch {
        /* 调用关系加载失败不影响查看代码 */
      }
    },

    goToSymbolId: async (symbolId) => {
      const { repoId } = get()
      if (!repoId) return
      try {
        const g = await codeApi.graph(repoId, symbolId)
        await get().selectSymbol(g.symbol)
      } catch (err) {
        toast.error('无法定位到该函数', errorMessage(err))
      }
    },

    goToLines: async (path, a, b) => {
      await get().openFile(path, { a, b })
    },

    explain: async (target, label, question = '') => {
      const { repoId } = get()
      if (!repoId) return
      explainCtrl?.abort()
      const ctrl = new AbortController()
      explainCtrl = ctrl
      const id = ++explainSeq
      set({ sideTab: 'explain', explainRun: { id, target, label, question, text: '', status: 'streaming', meta: null, escalatedTo: null, error: null } })
      const patch = (p: Partial<ExplainRun>): void => {
        const run = get().explainRun
        if (run && run.id === id) set({ explainRun: { ...run, ...p } })
      }
      try {
        await codeApi.explain(
          { repo_id: repoId, question, ...target },
          (e) => {
            const run = get().explainRun
            if (!run || run.id !== id) return
            if (e.event === 'meta') patch({ meta: e.data })
            else if (e.event === 'delta') patch({ text: run.text + e.data.text })
            else if (e.event === 'escalate') patch({ text: '', escalatedTo: e.data.model })
            else if (e.event === 'done') {
              set({ cards: [...get().cards, e.data.card], explainRun: null })
            } else if (e.event === 'error') patch({ status: 'failed', error: { code: e.data.code, message: e.data.message } })
          },
          ctrl.signal
        )
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') patch({ status: 'stopped' })
        else patch({ status: 'failed', error: { code: 'network', message: errorMessage(err) } })
      }
    },

    stopExplain: () => {
      explainCtrl?.abort()
    },
    dismissExplain: () => set({ explainRun: null }),

    removeCard: async (id) => {
      await codeApi.removeCard(id)
      set({ cards: get().cards.filter((c) => c.id !== id) })
    },

    loadFlow: async (rootId, depth) => {
      const { repoId } = get()
      if (!repoId) return
      const d = depth ?? get().flowDepth
      set({ flowLoading: true, flowError: null, flowDepth: d })
      try {
        const flow = await codeApi.flow(repoId, rootId ?? get().flowRoot, d)
        if (get().repoId !== repoId) return
        set({ flow, flowRoot: flow.root, flowLoading: false, flowSelected: flow.nodes[0]?.id ?? null })
      } catch (err) {
        set({ flowLoading: false, flowError: errorMessage(err) })
      }
    },

    selectFlowNode: (id) => set({ flowSelected: id }),

    annotateFlow: async () => {
      const { repoId, flowRoot } = get()
      if (!repoId) return
      set({ annotating: true })
      try {
        const flow = await codeApi.annotateFlow(repoId, flowRoot)
        set({ flow })
        toast.success('已让 AI 给流程节点命名', '名称与说明标注为 AI 生成，流程结构仍来自静态分析。')
      } catch (err) {
        toast.error('AI 命名失败', errorMessage(err))
      } finally {
        set({ annotating: false })
      }
    },

    loadMapping: async () => {
      const { repoId, paperId } = get()
      if (!repoId || !paperId) return
      try {
        const [mods, links] = await Promise.all([codeApi.modules(paperId), codeApi.links(repoId)])
        if (get().repoId === repoId) set({ modules: mods.items, links: links.items, mappingLoaded: true })
      } catch (err) {
        toast.error('加载论文↔代码关联失败', errorMessage(err))
      }
    },

    associate: async (opts) => {
      const { repoId } = get()
      if (!repoId) return
      const auto = !!opts?.auto
      set({ associating: true })
      try {
        const { job_id } = await codeApi.associate(repoId, !auto)
        const job = await watchJob(job_id)
        if (auto) {
          if (job.status !== 'completed') toast.error('没能自动生成关联', job.error ?? '任务未完成')
        } else if (job.status === 'completed') {
          const r = job.result as { modules?: number; links?: number; ai_used?: boolean } | null
          toast.success('已生成论文 ↔ 代码关联', `${r?.modules ?? 0} 个论文模块，${r?.links ?? 0} 条关联${r?.ai_used ? '' : '，未使用 AI，仅词法匹配，都标为“可能关联”'}`)
        } else toast.error('生成关联失败', job.error ?? '任务未完成')
        await get().loadMapping()
      } catch (err) {
        toast.error('生成关联失败', errorMessage(err))
      } finally {
        set({ associating: false })
      }
    },

    setLinkStatus: async (id, status) => {
      try {
        const l = await codeApi.setLink(id, status)
        const sp = get().symbolPaper
        set({
          links: get().links.map((x) => (x.id === id ? { ...x, status: l.status } : x)),
          symbolPaper: sp ? { ...sp, links: sp.links.map((x) => (x.id === id ? { ...x, status: l.status } : x)) } : sp
        })
      } catch (err) {
        toast.error('操作失败', errorMessage(err))
      }
    },

    loadConsistency: async () => {
      const { repoId } = get()
      if (!repoId) return
      try {
        const c = await codeApi.consistency(repoId)
        if (get().repoId === repoId) set({ consistency: c })
      } catch (err) {
        toast.error('加载一致性报告失败', errorMessage(err))
      }
    },

    runConsistency: async () => {
      const { repoId } = get()
      if (!repoId) return
      set({ checking: true })
      try {
        const { job_id } = await codeApi.runConsistency(repoId)
        const job = await watchJob(job_id)
        if (job.status !== 'completed') toast.error('一致性检查失败', job.error ?? '任务未完成')
        await get().loadConsistency()
      } catch (err) {
        toast.error('一致性检查失败', errorMessage(err))
      } finally {
        set({ checking: false })
      }
    }
  }
})

/** 打开论文阅读器并定位到某页（并尝试高亮对应段落）。 */
export async function openPaperAt(
  navigate: (to: string) => void,
  paperId: string,
  at: { page?: number; ord?: number }
): Promise<void> {
  const { api } = await import('@/lib/api')
  const { useReaderStore } = await import('@/features/reader/readerStore')
  if (at.page) {
    try {
      await api.papers.patch(paperId, { reading_page: at.page })
    } catch {
      /* 定位失败不阻止打开 */
    }
  }
  navigate(`/reader/${paperId}`)
  if (at.ord === undefined) return
  // 等阅读器加载完毕后再精确定位到段落
  const start = Date.now()
  const timer = window.setInterval(() => {
    const s = useReaderStore.getState()
    if (s.paper?.id === paperId && s.numPages > 0) {
      window.clearInterval(timer)
      void s.jumpToOrd(at.ord as number)
    } else if (Date.now() - start > 10_000) window.clearInterval(timer)
  }, 250)
}
