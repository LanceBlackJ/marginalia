import { create } from 'zustand'

/** 正文里点了“Fig. 3”“Table 2”这类引用后要打开的图或表（键是 figRefs 规范化后的键）。 */
export interface FigTarget {
  kind: 'fig' | 'table'
  key: string
}

interface FigViewerState {
  target: FigTarget | null
  /** 点了行内公式后要放大显示的 LaTeX */
  formula: string | null
  open: (t: FigTarget) => void
  openFormula: (tex: string) => void
  close: () => void
}

export const useFigViewer = create<FigViewerState>((set) => ({
  target: null,
  formula: null,
  open: (target) => set({ target }),
  openFormula: (formula) => set({ formula }),
  close: () => set({ target: null, formula: null })
}))
