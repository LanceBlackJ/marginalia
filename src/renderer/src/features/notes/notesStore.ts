import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import { toast } from '@/components/ui'
import type { Note, NoteCreate } from '@/lib/types'
import { getDraft, setDraft } from '@/lib/drafts'

export interface NoteDraft {
  quote: string | null
  page: number | null
  passage_ord: number | null
  card_id: string | null
  text: string
}

interface NotesState {
  paperId: string | null
  notes: Note[]
  loading: boolean
  error: string | null
  draft: NoteDraft | null

  load: (paperId: string) => Promise<void>
  reset: () => void
  startDraft: (d: Partial<NoteDraft>) => void
  setDraftText: (text: string) => void
  cancelDraft: () => void
  saveDraft: () => Promise<void>
  create: (body: NoteCreate) => Promise<Note | null>
  update: (id: string, text: string) => Promise<void>
  remove: (id: string) => Promise<void>
}

export const useNotesStore = create<NotesState>((set, get) => ({
  paperId: null,
  notes: [],
  loading: false,
  error: null,
  draft: null,

  load: async (paperId) => {
    // 上次没写完的新笔记：回到这篇论文时原样还在
    const saved = getDraft<NoteDraft>('note', paperId)
    set({ paperId, notes: [], loading: true, error: null, draft: saved && (saved.text?.trim() || saved.quote) ? saved : null })
    try {
      const { items } = await api.notes.list(paperId)
      if (get().paperId === paperId) set({ notes: items, loading: false })
    } catch (err) {
      if (get().paperId === paperId) set({ loading: false, error: errorMessage(err) })
    }
  },

  reset: () => set({ paperId: null, notes: [], loading: false, error: null, draft: null }),

  startDraft: (d) => {
    const draft = { quote: null, page: null, passage_ord: null, card_id: null, text: '', ...d }
    set({ draft })
    setDraft('note', get().paperId, draft)
  },
  setDraftText: (text) => {
    const cur = get().draft
    if (!cur) return
    const draft = { ...cur, text }
    set({ draft })
    setDraft('note', get().paperId, draft)
  },
  cancelDraft: () => {
    set({ draft: null })
    setDraft('note', get().paperId, null)
  },

  saveDraft: async () => {
    const d = get().draft
    if (!d) return
    if (!d.text.trim() && !d.quote) return
    const note = await get().create({ text: d.text.trim(), page: d.page, quote: d.quote, passage_ord: d.passage_ord, card_id: d.card_id })
    if (note) {
      set({ draft: null })
      setDraft('note', get().paperId, null)
    }
  },

  create: async (body) => {
    const paperId = get().paperId
    if (!paperId) return null
    try {
      const note = await api.notes.create(paperId, body)
      set((s) => ({ notes: [note, ...s.notes] }))
      return note
    } catch (err) {
      toast.error('保存笔记失败', errorMessage(err))
      return null
    }
  },

  update: async (id, text) => {
    try {
      const note = await api.notes.update(id, text)
      set((s) => ({ notes: s.notes.map((n) => (n.id === id ? note : n)) }))
    } catch (err) {
      toast.error('更新笔记失败', errorMessage(err))
    }
  },

  remove: async (id) => {
    const prev = get().notes
    set({ notes: prev.filter((n) => n.id !== id) })
    try {
      await api.notes.remove(id)
    } catch (err) {
      set({ notes: prev })
      toast.error('删除笔记失败', errorMessage(err))
    }
  }
}))
