import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { SearchHit } from '@/lib/types'
import { useReaderStore } from './readerStore'

interface SearchState {
  query: string
  hits: SearchHit[]
  active: number
  loading: boolean
  error: string | null
  searched: boolean

  setQuery: (q: string) => void
  run: (paperId: string, q: string) => Promise<void>
  go: (index: number) => void
  step: (dir: 1 | -1) => void
  clear: () => void
}

let controller: AbortController | null = null

export const useSearchStore = create<SearchState>((set, get) => ({
  query: '',
  hits: [],
  active: -1,
  loading: false,
  error: null,
  searched: false,

  setQuery: (query) => set({ query }),

  run: async (paperId, q) => {
    controller?.abort()
    const query = q.trim()
    if (!query) {
      set({ hits: [], active: -1, loading: false, error: null, searched: false })
      return
    }
    controller = new AbortController()
    const signal = controller.signal
    set({ loading: true, error: null })
    try {
      const { items } = await api.papers.search(paperId, query, 80, signal)
      if (signal.aborted) return
      set({ hits: items, active: -1, loading: false, searched: true })
      if (items.length > 0) get().go(0)
    } catch (err) {
      if (signal.aborted) return
      set({ loading: false, error: errorMessage(err), hits: [], active: -1, searched: true })
    }
  },

  go: (index) => {
    const { hits } = get()
    if (hits.length === 0) return
    const i = ((index % hits.length) + hits.length) % hits.length
    set({ active: i })
    const h = hits[i]
    useReaderStore.getState().jumpToEvidence({ page: h.page, bbox: h.bbox, ord: h.ord })
  },

  step: (dir) => {
    const { active, hits } = get()
    if (hits.length === 0) return
    get().go(active < 0 ? 0 : active + dir)
  },

  clear: () => {
    controller?.abort()
    set({ query: '', hits: [], active: -1, loading: false, error: null, searched: false })
  }
}))
