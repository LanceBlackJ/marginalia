import { create } from 'zustand'
import { errorMessage } from '@/lib/api'
import { discoveryApi } from '@/lib/api.discovery'
import { loadJSON, saveJSON } from '@/lib/storage'
import type { DiscoveryMode, SearchResponse, Work } from '@/lib/types.discovery'

type Status = 'idle' | 'loading' | 'done' | 'error'

interface DiscoveryState {
  query: string
  mode: DiscoveryMode
  ai: boolean
  yearFrom: string
  yearTo: string
  seedPaperId: string
  status: Status
  error: string | null
  data: SearchResponse | null
  set: (patch: Partial<Pick<DiscoveryState, 'query' | 'mode' | 'ai' | 'yearFrom' | 'yearTo' | 'seedPaperId'>>) => void
  search: () => Promise<void>
  patchWork: (key: string, patch: Partial<Work>) => void
}

let ctrl: AbortController | null = null

function yearOf(s: string): number | null {
  const n = Number(s)
  return Number.isInteger(n) && n >= 1900 && n <= 2100 ? n : null
}

export const useDiscovery = create<DiscoveryState>((set, get) => ({
  query: '',
  mode: 'comprehensive',
  ai: true,
  yearFrom: '',
  yearTo: '',
  seedPaperId: '',
  status: 'idle',
  error: null,
  data: null,

  set: (patch) => set(patch),

  search: async () => {
    const s = get()
    const hasSeed = s.mode === 'related' && s.seedPaperId
    if (!s.query.trim() && !hasSeed) {
      set({ error: s.mode === 'related' ? '请输入基准论文的标题或 DOI，或从文献库选择一篇。' : '请输入要检索的内容。', status: 'error' })
      return
    }
    ctrl?.abort()
    ctrl = new AbortController()
    set({ status: 'loading', error: null })
    try {
      const data = await discoveryApi.search(
        {
          query: s.query.trim(),
          mode: s.mode,
          year_from: yearOf(s.yearFrom),
          year_to: yearOf(s.yearTo),
          limit: 100,
          ai: s.ai,
          seed: s.mode === 'related' ? (hasSeed ? { paper_id: s.seedPaperId } : { title: s.query.trim(), doi: /^10\.\d{4,}\//.test(s.query.trim()) ? s.query.trim() : null }) : null
        },
        ctrl.signal
      )
      set({ data, status: 'done' })
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      set({ status: 'error', error: errorMessage(err) })
    }
  },

  patchWork: (key, patch) =>
    set((s) => (s.data ? { data: { ...s.data, results: s.data.results.map((w) => (w.key === key ? { ...w, ...patch } : w)) } } : s))
}))

// ---------------- 收藏与比较篮（跨页面共享） ----------------
interface SavedState {
  keys: Set<string>
  loaded: boolean
  items: Work[]
  load: () => Promise<void>
  toggle: (w: Work) => Promise<void>
}

export const useSaved = create<SavedState>((set, get) => ({
  keys: new Set(),
  items: [],
  loaded: false,
  load: async () => {
    try {
      const { items } = await discoveryApi.saved()
      set({ items, keys: new Set(items.map((i) => i.key)), loaded: true })
    } catch {
      set({ loaded: true })
    }
  },
  toggle: async (w) => {
    const has = get().keys.has(w.key)
    const next = new Set(get().keys)
    if (has) next.delete(w.key)
    else next.add(w.key)
    set({ keys: next })
    try {
      if (has) await discoveryApi.unsave(w.key)
      else await discoveryApi.save(w)
      void get().load()
    } catch (err) {
      const back = new Set(get().keys)
      if (has) back.add(w.key)
      else back.delete(w.key)
      set({ keys: back })
      throw err
    }
  }
}))

const BASKET_KEY = 'pd.compareBasket'

interface BasketState {
  ids: string[]
  toggle: (id: string) => void
  remove: (id: string) => void
  clear: () => void
}

export const useBasket = create<BasketState>((set, get) => ({
  ids: (() => {
    const v = loadJSON<unknown>(BASKET_KEY, [])
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(0, 8) : []
  })(),
  toggle: (id) => {
    const ids = get().ids.includes(id) ? get().ids.filter((x) => x !== id) : [...get().ids, id].slice(0, 8)
    saveJSON(BASKET_KEY, ids)
    set({ ids })
  },
  remove: (id) => {
    const ids = get().ids.filter((x) => x !== id)
    saveJSON(BASKET_KEY, ids)
    set({ ids })
  },
  clear: () => {
    saveJSON(BASKET_KEY, [])
    set({ ids: [] })
  }
}))
