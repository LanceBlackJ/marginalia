import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import { toast } from '@/components/ui'
import type { PageRect, ParaInsight, ParaInsightResponse } from '@/lib/types'

export type ParaPassage = ParaInsightResponse['passage']

interface ParaState {
  /** 正在看的段落序号；null = 没有打开 */
  ord: number | null
  /** 分析返回后才有：段落所在页和位置（PDF 里据此高亮） */
  passage: ParaPassage | null
  /** 这一段在 PDF 里真实占的位置（跨栏、跨页的续行也包含）；还没取回时用 passage.bbox */
  rects: PageRect[] | null
  status: 'idle' | 'loading' | 'ready' | 'error'
  insight: ParaInsight | null
  error: string | null
  open: (paperId: string, ord: number, force?: boolean) => Promise<void>
  /** 从选中的文字找到它所在的段落再打开（PDF 里的选区没有段落序号） */
  openFromSelection: (paperId: string, sel: { text: string; page: number; ord?: number }) => Promise<void>
  close: () => void
}

let seq = 0

export const useParaStore = create<ParaState>((set, get) => ({
  ord: null,
  passage: null,
  rects: null,
  status: 'idle',
  insight: null,
  error: null,

  open: async (paperId, ord, force = false) => {
    const mine = ++seq
    set({ ord, status: 'loading', error: null, ...(get().ord === ord ? {} : { passage: null, rects: null, insight: null }) })
    try {
      const r = await api.papers.insight(paperId, ord, force)
      if (mine !== seq) return
      set({ ord, passage: r.passage, insight: r.insight, status: 'ready' })
      try {
        const pos = await api.papers.passageRects(paperId, ord)
        if (mine === seq) set({ rects: pos.rects })
      } catch {
        /* 取不到精确位置就用起始块的坐标 */
      }
    } catch (err) {
      if (mine !== seq) return
      set({ status: 'error', error: errorMessage(err) })
    }
  },

  openFromSelection: async (paperId, sel) => {
    if (sel.ord !== undefined && sel.ord !== null) return get().open(paperId, sel.ord)
    try {
      const { matches } = await api.papers.locate(paperId, sel.text, sel.page)
      const best = matches[0]
      if (!best || best.score < 0.5) {
        toast.error('没找到这段话在论文里的位置', '换一段选中的文字试试')
        return
      }
      await get().open(paperId, best.ord)
    } catch (err) {
      toast.error('没能分析这一段', errorMessage(err))
    }
  },

  close: () => {
    seq++
    set({ ord: null, passage: null, rects: null, status: 'idle', insight: null, error: null })
  }
}))
