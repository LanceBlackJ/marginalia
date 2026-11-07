import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import { toast } from '@/components/ui'
import type { Annotation, Bookmark, HighlightColor } from '@/lib/types'
import type { SelectionInfo } from './pdf/selection'

interface ActiveAnnotation {
  id: string
  /** 视口坐标，用于放置小浮层 */
  x: number
  y: number
}

interface MarksState {
  paperId: string | null
  annotations: Annotation[]
  bookmarks: Bookmark[]
  activeAnnotation: ActiveAnnotation | null

  load: (paperId: string) => Promise<void>
  reset: () => void
  addHighlight: (sel: SelectionInfo, color: HighlightColor) => Promise<void>
  removeAnnotation: (id: string) => Promise<void>
  setActiveAnnotation: (a: ActiveAnnotation | null) => void
  /** at 有值 = 书签落在正文的某一段（右键添加）；没有 = 只记页码 */
  addBookmark: (page: number, label: string, at?: { passage_ord: number; quote: string }) => Promise<void>
  removeBookmark: (id: string) => Promise<void>
}

export const useMarksStore = create<MarksState>((set, get) => ({
  paperId: null,
  annotations: [],
  bookmarks: [],
  activeAnnotation: null,

  load: async (paperId) => {
    set({ paperId, annotations: [], bookmarks: [], activeAnnotation: null })
    const [a, b] = await Promise.allSettled([api.annotations.list(paperId), api.bookmarks.list(paperId)])
    if (get().paperId !== paperId) return
    set({
      annotations: a.status === 'fulfilled' ? a.value.items : [],
      bookmarks: b.status === 'fulfilled' ? b.value.items : []
    })
  },

  reset: () => set({ paperId: null, annotations: [], bookmarks: [], activeAnnotation: null }),

  addHighlight: async (sel, color) => {
    const paperId = get().paperId
    if (!paperId) return
    try {
      const a = await api.annotations.create(paperId, { page: sel.page, kind: 'highlight', color, text: sel.text, rects: sel.rects })
      set((s) => ({ annotations: [...s.annotations, a] }))
    } catch (err) {
      toast.error('添加高亮失败', errorMessage(err))
    }
  },

  removeAnnotation: async (id) => {
    const paperId = get().paperId
    if (!paperId) return
    const prev = get().annotations
    set({ annotations: prev.filter((a) => a.id !== id), activeAnnotation: null })
    try {
      await api.annotations.remove(paperId, id)
    } catch (err) {
      set({ annotations: prev })
      toast.error('删除高亮失败', errorMessage(err))
    }
  },

  setActiveAnnotation: (activeAnnotation) => set({ activeAnnotation }),

  addBookmark: async (page, label, at) => {
    const paperId = get().paperId
    if (!paperId) return
    // 同一段不重复加；整页书签（没有段落位置的）同一页也不重复加
    const dup = get().bookmarks.some((b) => (at ? b.passage_ord === at.passage_ord : b.page === page && b.passage_ord === null))
    if (dup) {
      toast.info(at ? '这一段已经有书签了' : '这一页已经有书签了')
      return
    }
    try {
      const b = await api.bookmarks.create(paperId, page, label, at)
      set((s) => ({ bookmarks: [...s.bookmarks, b].sort((x, y) => x.page - y.page || (x.passage_ord ?? -1) - (y.passage_ord ?? -1)) }))
      toast.success('已添加书签', `第 ${page} 页`)
    } catch (err) {
      toast.error('添加书签失败', errorMessage(err))
    }
  },

  removeBookmark: async (id) => {
    const paperId = get().paperId
    if (!paperId) return
    const prev = get().bookmarks
    set({ bookmarks: prev.filter((b) => b.id !== id) })
    try {
      await api.bookmarks.remove(paperId, id)
    } catch (err) {
      set({ bookmarks: prev })
      toast.error('删除书签失败', errorMessage(err))
    }
  }
}))
