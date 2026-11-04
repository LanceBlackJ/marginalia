import { create } from 'zustand'

/** 正文里点了一张便签标记：要显示哪张笔记、在哪里显示。 */
interface PeekState {
  peek: { id: string; x: number; y: number } | null
  open: (id: string, x: number, y: number) => void
  close: () => void
}

export const useNotePeek = create<PeekState>((set) => ({
  peek: null,
  open: (id, x, y) => set({ peek: { id, x, y } }),
  close: () => set({ peek: null })
}))
