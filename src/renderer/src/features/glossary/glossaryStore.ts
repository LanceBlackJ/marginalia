import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { GlossaryData, GlossTerm } from '@/lib/types'

interface GlossaryState {
  paperId: string | null
  terms: GlossTerm[]
  status: GlossaryData['status'] | null
  error: string | null
  /** 正文里点了某个词：术语标签页展开并滚到它 */
  focus: string | null
  /** 打开论文时加载；还没有（或上次失败）就让后台补做，做的过程中定时刷新 */
  load: (paperId: string) => Promise<void>
  retry: () => Promise<void>
  setFocus: (term: string | null) => void
  reset: () => void
}

let timer = 0
let seq = 0

export const useGlossaryStore = create<GlossaryState>((set, get) => ({
  paperId: null,
  terms: [],
  status: null,
  error: null,
  focus: null,

  load: async (paperId) => {
    window.clearTimeout(timer)
    const mine = ++seq
    if (get().paperId !== paperId) set({ paperId, terms: [], status: null, error: null, focus: null })
    const once = async (first: boolean): Promise<void> => {
      try {
        const d = await api.papers.glossary(paperId)
        if (mine !== seq) return
        set({ terms: d.terms, status: d.status, error: null })
        if (first && (d.status.state === 'pending' || d.status.state === 'failed')) {
          try {
            await api.papers.glossaryStart(paperId)
          } catch {
            /* 没配置模型等：状态里如实写明 */
          }
          if (mine === seq) return once(false)
          return
        }
        if (d.status.state === 'running' || d.status.state === 'pending') {
          timer = window.setTimeout(() => void once(false), 3500)
        }
      } catch (err) {
        if (mine === seq) set({ error: errorMessage(err) })
      }
    }
    await once(true)
  },

  retry: async () => {
    const id = get().paperId
    if (!id) return
    try {
      await api.papers.glossaryStart(id)
    } catch {
      /* 状态里会写明原因 */
    }
    await get().load(id)
  },

  setFocus: (focus) => set({ focus }),

  reset: () => {
    seq++
    window.clearTimeout(timer)
    set({ paperId: null, terms: [], status: null, error: null, focus: null })
  }
}))
