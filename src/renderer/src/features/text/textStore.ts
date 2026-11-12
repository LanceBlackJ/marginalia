import { create } from 'zustand'
import { toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { textApi, type EquationLatexItem, type EquationsLatex, type FigureRegion, type TableAnalysisItem, type TableTranslation, type TablesAnalysis, type TextItem } from '@/lib/api.text'
import { visionApi } from '@/lib/api.vision'
import type { EquationItem } from '@/lib/types.vision'

export interface EquationInfo {
  id: number
  number: string | null
  /** 已经识别好的 LaTeX（还没识别或识别失败为 null） */
  latex: string | null
  confidence: 'high' | 'medium' | 'low' | null
  status: 'none' | 'ok' | 'failed'
  warnings: string[]
  error: string | null
}

export interface EqProgress {
  total: number
  done: number
  failed: number
  complete: boolean
}

/** 把整篇识别结果合并进“段落序号 → 公式”表（没有识别结果的公式保持“还没识别”）。 */
export function mergeEquations(list: EquationItem[], res: EquationsLatex | null): Map<number, EquationInfo> {
  const byId = new Map<number, EquationLatexItem>((res?.items ?? []).map((i) => [i.equation_id, i]))
  const out = new Map<number, EquationInfo>()
  for (const e of list) {
    if (e.passage_ord === null) continue
    const r = byId.get(e.id)
    out.set(e.passage_ord, {
      id: e.id,
      number: e.number,
      latex: r?.status === 'ok' ? r.latex : null,
      confidence: r?.confidence ?? null,
      status: r?.status ?? 'none',
      warnings: r?.warnings ?? [],
      error: r?.error ?? null
    })
  }
  return out
}

const autoEqStarted = new Set<string>()
const autoTblStarted = new Set<string>()

/** 表格相关状态的初始值（切换论文、重置时整体还原）。 */
const TBL_INIT = {
  tablesByCaption: new Map<number, TableAnalysisItem>(),
  /** 已经识别成表格的那些表：它们的“行文字”段落不再单独显示 */
  tableHidden: new Set<number>(),
  tbl: { total: 0, ready: 0, failed: 0, running: 0, complete: false },
  tblJobId: null as string | null,
  tblProgress: undefined as number | undefined,
  tblMsg: '',
  tblError: null as string | null
}

/** 识别成功的表格：表格主体的行文字段落序号集合（文字界面用表格替代它们）。 */
export function hiddenTableOrds(res: TablesAnalysis | null): Set<number> {
  const out = new Set<number>()
  for (const t of res?.items ?? []) if (t.status === 'ready' && t.data) for (const o of t.body_ords) out.add(o)
  return out
}

interface TextState {
  paperId: string | null
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: string | null
  items: TextItem[]
  /** 手里这一版段落的版本号（后端给的）：和译文那边的版本号对不上，说明段落已经过期（比如重新解析过） */
  revision: string | null

  trans: Map<number, string>
  /** 已翻译的表格（键：表题序号） */
  tableTr: Record<string, TableTranslation>
  /** 已翻译的标题（键：原文标题） */
  headingTr: Record<string, string>
  trTotal: number
  trDone: number
  trComplete: boolean
  trModels: string[]
  jobId: string | null
  jobMsg: string
  progress: number | undefined

  equationsByOrd: Map<number, EquationInfo>
  figuresByCaption: Map<number, FigureRegion>

  eq: EqProgress
  eqList: EquationItem[]
  eqJobId: string | null
  eqProgress: number | undefined
  eqMsg: string
  /** 识别任务整体失败的原因（比如没有 API Key、没配置视觉模型） */
  eqError: string | null

  tablesByCaption: Map<number, TableAnalysisItem>
  tableHidden: Set<number>
  tbl: { total: number; ready: number; failed: number; running: number; complete: boolean }
  tblJobId: string | null
  tblProgress: number | undefined
  tblMsg: string
  /** 表格识别任务整体失败的原因（没有 API Key、没配置视觉模型等） */
  tblError: string | null
  /** 正在“重新翻译这一段”的段落序号 */
  retranslating: Set<number>

  refreshTables: () => Promise<void>
  startTables: (manual?: boolean, force?: boolean) => Promise<void>
  retranslate: (ord: number) => Promise<void>
  refreshEquations: () => Promise<void>
  startEquations: (manual?: boolean) => Promise<void>
  load: (paperId: string) => Promise<void>
  reset: () => void
  refreshTranslation: () => Promise<void>
  startTranslation: () => Promise<void>
  cancelTranslation: () => Promise<void>
}

/** 正在因为“段落过期”而重新载入：载入过程中取译文不要再触发一次重新载入。 */
let reloadingStale = false
let pollTimer: number | undefined

function stopPolling(): void {
  window.clearTimeout(pollTimer)
  pollTimer = undefined
}

export const useTextStore = create<TextState>((set, get) => {
  const poll = (jobId: string, paperId: string): void => {
    stopPolling()
    const tick = async (): Promise<void> => {
      if (get().paperId !== paperId || get().jobId !== jobId) return
      try {
        const j = await api.jobs.get(jobId)
        if (get().paperId !== paperId || get().jobId !== jobId) return
        set({ jobMsg: j.message || '正在翻译…', progress: j.progress })
        await get().refreshTranslation()
        if (j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled') {
          set({ jobId: null, progress: undefined })
          if (j.status === 'completed') toast.success('全文翻译完成')
          else if (j.status === 'failed') toast.error('全文翻译失败', j.error ?? '请到任务页查看原因')
          return
        }
      } catch {
        /* 这一次没取到进度，下一次再试 */
      }
      pollTimer = window.setTimeout(() => void tick(), 1500)
    }
    pollTimer = window.setTimeout(() => void tick(), 600)
  }

  const pollEq = (jobId: string, paperId: string): void => {
    const tick = async (): Promise<void> => {
      if (get().paperId !== paperId || get().eqJobId !== jobId) return
      try {
        const j = await api.jobs.get(jobId)
        if (get().paperId !== paperId || get().eqJobId !== jobId) return
        set({ eqMsg: j.message || '正在识别公式…', eqProgress: j.progress })
        await get().refreshEquations()
        if (j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled') {
          set({ eqJobId: null, eqProgress: undefined, eqError: j.status === 'failed' ? (j.error ?? '公式识别失败') : null })
          return
        }
      } catch {
        /* 这一次没取到进度，下一次再试 */
      }
      window.setTimeout(() => void tick(), 1500)
    }
    window.setTimeout(() => void tick(), 600)
  }

  const pollTbl = (jobId: string, paperId: string): void => {
    const tick = async (): Promise<void> => {
      if (get().paperId !== paperId || get().tblJobId !== jobId) return
      try {
        const j = await api.jobs.get(jobId)
        if (get().paperId !== paperId || get().tblJobId !== jobId) return
        set({ tblMsg: j.message || '正在识别表格…', tblProgress: j.progress })
        await get().refreshTables()
        if (j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled') {
          set({ tblJobId: null, tblProgress: undefined, tblError: j.status === 'failed' ? (j.error ?? '表格识别失败') : null })
          return
        }
      } catch {
        /* 这一次没取到进度，下一次再试 */
      }
      window.setTimeout(() => void tick(), 1500)
    }
    window.setTimeout(() => void tick(), 600)
  }

  return {
    ...TBL_INIT,
    retranslating: new Set<number>(),
    paperId: null,
    status: 'idle',
    error: null,
    items: [],
    revision: null,
    trans: new Map(),
    tableTr: {},
    headingTr: {},
    trTotal: 0,
    trDone: 0,
    trComplete: false,
    trModels: [],
    jobId: null,
    jobMsg: '',
    progress: undefined,
    equationsByOrd: new Map(),
    figuresByCaption: new Map(),
    eq: { total: 0, done: 0, failed: 0, complete: false },
    eqList: [],
    eqJobId: null,
    eqProgress: undefined,
    eqMsg: '',
    eqError: null,

    refreshEquations: async () => {
      const id = get().paperId
      if (!id) return
      try {
        const res = await textApi.equationsLatex(id)
        if (get().paperId !== id) return
        set({
          eq: { total: res.total, done: res.done, failed: res.failed, complete: res.complete },
          equationsByOrd: mergeEquations(get().eqList, res)
        })
      } catch {
        /* 公式进度读不到不影响阅读：公式显示截图 */
      }
    },

    startEquations: async (manual = false) => {
      const id = get().paperId
      if (!id || get().eqJobId) return
      try {
        const r = await textApi.startEquationsLatex(id)
        if (r.job_id) {
          set({ eqJobId: r.job_id, eqMsg: '正在识别公式…', eqProgress: undefined, eqError: null })
          pollEq(r.job_id, id)
        } else {
          await get().refreshEquations()
          if (manual) toast.info('没有需要识别的公式')
        }
      } catch (err) {
        set({ eqError: errorMessage(err) })
        if (manual) toast.error('无法开始识别公式', errorMessage(err))
      }
    },

    refreshTables: async () => {
      const id = get().paperId
      if (!id) return
      try {
        const res = await textApi.tables(id)
        if (get().paperId !== id) return
        set({
          tbl: { total: res.total, ready: res.ready, failed: res.failed, running: res.running, complete: res.complete },
          tablesByCaption: new Map(res.items.map((t) => [t.caption_ord, t])),
          tableHidden: hiddenTableOrds(res)
        })
      } catch {
        /* 表格进度读不到不影响阅读：表格行文字照常显示 */
      }
    },

    startTables: async (manual = false, force = false) => {
      const id = get().paperId
      if (!id || get().tblJobId) return
      try {
        const r = await textApi.startTables(id, force)
        if (r.job_id) {
          set({ tblJobId: r.job_id, tblMsg: '正在识别表格…', tblProgress: undefined, tblError: null })
          await get().refreshTables()
          pollTbl(r.job_id, id)
        } else {
          await get().refreshTables()
          if (manual) toast.info('没有需要识别的表格')
        }
      } catch (err) {
        set({ tblError: errorMessage(err) })
        if (manual) toast.error('无法开始识别表格', errorMessage(err))
      }
    },

    /** “重新翻译这一段”：强制重译并写回缓存，随后刷新译文。 */
    retranslate: async (ord) => {
      const id = get().paperId
      const item = get().items.find((i) => i.ord === ord)
      if (!id || !item || get().retranslating.has(ord)) return
      set({ retranslating: new Set(get().retranslating).add(ord) })
      let failure: string | null = null
      try {
        await api.ai.translate({ paper_id: id, text: item.text, page: item.page, passage_ord: item.ord, force: true, math: item.math }, (event, data) => {
          if (event === 'error') failure = String((data as Record<string, unknown>).message ?? '翻译失败')
        })
      } catch (err) {
        failure = errorMessage(err)
      }
      const next = new Set(get().retranslating)
      next.delete(ord)
      set({ retranslating: next })
      if (failure) toast.error('重新翻译失败', failure)
      else await get().refreshTranslation()
    },

    load: async (paperId) => {
      stopPolling()
      set({
        ...TBL_INIT, retranslating: new Set<number>(),
        paperId, status: 'loading', error: null, items: [], revision: null, trans: new Map(), tableTr: {}, headingTr: {}, trTotal: 0, trDone: 0, trComplete: false, trModels: [],
        jobId: null, jobMsg: '', progress: undefined, equationsByOrd: new Map(), figuresByCaption: new Map(),
        eq: { total: 0, done: 0, failed: 0, complete: false }, eqList: [], eqJobId: null, eqProgress: undefined, eqMsg: '', eqError: null
      })
      try {
        const t = await textApi.text(paperId)
        if (get().paperId !== paperId) return
        set({ items: t.items, revision: t.revision ?? null, status: 'ready' })
      } catch (err) {
        if (get().paperId === paperId) set({ status: 'error', error: errorMessage(err) })
        return
      }
      await get().refreshTranslation()
      // 之前开始的翻译任务还在跑（离开又回来）：接着显示进度
      try {
        const running = [...(await api.jobs.list({ status: 'running', type: 'translate_paper' })).items, ...(await api.jobs.list({ status: 'pending', type: 'translate_paper' })).items]
        const mine = running.find((j) => j.paper_id === paperId)
        if (mine && get().paperId === paperId) {
          set({ jobId: mine.id, jobMsg: mine.message || '正在翻译…', progress: mine.progress })
          poll(mine.id, paperId)
        }
      } catch {
        /* 任务列表取不到不影响阅读 */
      }
      // 图与公式：只读已有结果，不调用模型
      void (async () => {
        try {
          // 逻辑图：解析器切碎的子图已按图注归并，bbox 是整张图的外框（内联显示整张图，而不是其中一块）
          const figs = await visionApi.figures(paperId)
          if (get().paperId !== paperId) return
          const m = new Map<number, FigureRegion>()
          for (const f of figs.items) {
            if (f.caption_ord !== null && f.kind === 'figure') m.set(f.caption_ord, { id: f.id, page: f.page, bbox: f.bbox, kind: 'figure', label: f.label, caption_ord: f.caption_ord })
          }
          set({ figuresByCaption: m })
        } catch {
          /* 没有图区域就不提供“查看原图” */
        }
        try {
          const eqs = (await visionApi.equations(paperId)).items
          if (get().paperId !== paperId) return
          set({ eqList: eqs, equationsByOrd: mergeEquations(eqs, null) })
          await get().refreshEquations()
          if (get().paperId !== paperId || eqs.length === 0) return
          // 之前开始的识别任务还在跑：接着显示进度；否则还有没识别的公式就自动开始（每篇论文只自动开始一次）
          const running = [
            ...(await api.jobs.list({ status: 'running', type: 'equations_latex' })).items,
            ...(await api.jobs.list({ status: 'pending', type: 'equations_latex' })).items
          ].find((j) => j.paper_id === paperId)
          if (running) {
            set({ eqJobId: running.id, eqMsg: running.message || '正在识别公式…', eqProgress: running.progress })
            pollEq(running.id, paperId)
          } else if (!get().eq.complete && !autoEqStarted.has(paperId)) {
            autoEqStarted.add(paperId)
            await get().startEquations(false)
          }
        } catch {
          /* 没有公式信息就只显示文字层 */
        }
      })()
      // 表格：先读已有结果（不调用模型）；还有没识别的就自动开始（每篇论文只自动开始一次）
      void (async () => {
        try {
          await get().refreshTables()
          if (get().paperId !== paperId || get().tbl.total === 0) return
          const running = [
            ...(await api.jobs.list({ status: 'running', type: 'tables_analysis' })).items,
            ...(await api.jobs.list({ status: 'pending', type: 'tables_analysis' })).items
          ].find((j) => j.paper_id === paperId)
          if (running) {
            set({ tblJobId: running.id, tblMsg: running.message || '正在识别表格…', tblProgress: running.progress })
            pollTbl(running.id, paperId)
          } else if (!get().tbl.complete && !autoTblStarted.has(paperId)) {
            autoTblStarted.add(paperId)
            await get().startTables(false)
          }
        } catch {
          /* 没有表格信息就只显示文字层 */
        }
      })()
    },

    reset: () => {
      stopPolling()
      set({
        ...TBL_INIT, retranslating: new Set<number>(),
        paperId: null, status: 'idle', error: null, items: [], revision: null, trans: new Map(), tableTr: {}, headingTr: {}, trTotal: 0, trDone: 0, trComplete: false, trModels: [],
        jobId: null, jobMsg: '', progress: undefined, equationsByOrd: new Map(), figuresByCaption: new Map(),
        eq: { total: 0, done: 0, failed: 0, complete: false }, eqList: [], eqJobId: null, eqProgress: undefined, eqMsg: '', eqError: null
      })
    },

    refreshTranslation: async () => {
      const id = get().paperId
      if (!id) return
      try {
        const t = await textApi.translation(id)
        if (get().paperId !== id) return
        // 手里的段落是旧版（例如阅读页开着的时候这篇论文被重新解析了，段落序号整体变了），而译文是按新版序号给的：
        // 直接配在一起整篇都会错位。先把段落重新载入成新版，load 会接着取译文。
        const mine = get().revision
        if (t.revision && mine && t.revision !== mine && !reloadingStale) {
          reloadingStale = true
          try {
            await get().load(id)
          } finally {
            reloadingStale = false
          }
          return
        }
        const cellsMissing = (t.cells_total ?? 0) - (t.cells_done ?? 0)
        set({
          trans: new Map(t.items.map((i) => [i.ord, i.translation])),
          tableTr: t.tables ?? {},
          headingTr: t.headings ?? {},
          trTotal: t.total,
          trDone: t.done,
          // 表格里还有没翻译的单元格，也算“还没翻完”，这样“继续翻译”不会消失
          trComplete: t.complete && cellsMissing <= 0,
          trModels: t.models
        })
      } catch {
        /* 译文进度读不到不影响原文阅读 */
      }
    },

    startTranslation: async () => {
      const id = get().paperId
      if (!id || get().jobId) return
      try {
        const r = await textApi.startTranslation(id)
        if (r.job_id) {
          set({ jobId: r.job_id, jobMsg: '正在翻译…', progress: undefined })
          toast.info('开始翻译全文', '正文段落会发送给你配置的模型；译文会边翻边出现，也可以到任务页查看。')
          poll(r.job_id, id)
        } else {
          await get().refreshTranslation()
          toast.info('这篇论文已经全部翻译过了')
        }
      } catch (err) {
        toast.error('无法开始翻译', errorMessage(err))
      }
    },

    cancelTranslation: async () => {
      const jobId = get().jobId
      if (!jobId) return
      try {
        await api.jobs.cancel(jobId)
      } catch (err) {
        toast.error('取消失败', errorMessage(err))
      }
    }
  }
})
