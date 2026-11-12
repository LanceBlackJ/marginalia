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
  /** 点了正文里的“Eq. (5)”后要放大显示的公式（公式的 id；显示的是它在 PDF 里的矢量图） */
  equationId: number | null
  open: (t: FigTarget) => void
  openFormula: (tex: string) => void
  openEquation: (id: number) => void
  close: () => void
}

export const useFigViewer = create<FigViewerState>((set) => ({
  target: null,
  formula: null,
  equationId: null,
  open: (target) => set({ target }),
  openFormula: (formula) => set({ formula }),
  openEquation: (equationId) => set({ equationId }),
  close: () => set({ target: null, formula: null, equationId: null })
}))
