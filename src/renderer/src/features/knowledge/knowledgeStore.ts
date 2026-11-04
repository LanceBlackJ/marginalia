import { create } from 'zustand'
import { kbApi } from '@/lib/api.discovery'
import { errorMessage } from '@/lib/api'
import type { KbTopic, TopicSuggestion } from '@/lib/types.discovery'

export type Scope = 'all' | 'unassigned' | string // 其余为主题 id

interface KbState {
  topics: KbTopic[]
  suggestions: TopicSuggestion[]
  loaded: boolean
  error: string | null
  scope: Scope
  setScope: (s: Scope) => void
  /** 条目/候选发生变化后递增，让各列表重新加载。 */
  version: number
  bump: () => void
  loadTopics: () => Promise<void>
  loadSuggestions: () => Promise<void>
}

export const useKb = create<KbState>((set, get) => ({
  topics: [],
  suggestions: [],
  loaded: false,
  error: null,
  scope: 'all',
  setScope: (scope) => set({ scope }),
  version: 0,
  bump: () => set((s) => ({ version: s.version + 1 })),
  loadTopics: async () => {
    try {
      const { items } = await kbApi.topics()
      set({ topics: items, loaded: true, error: null })
      // 当前所选主题被删除后退回“全部”
      const sc = get().scope
      if (sc !== 'all' && sc !== 'unassigned' && !items.some((t) => t.id === sc)) set({ scope: 'all' })
    } catch (err) {
      set({ loaded: true, error: errorMessage(err) })
    }
  },
  loadSuggestions: async () => {
    try {
      set({ suggestions: (await kbApi.topicSuggestions()).items })
    } catch {
      /* 建议加载失败不影响主流程 */
    }
  }
}))
