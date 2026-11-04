import { create } from 'zustand'

interface ShellState {
  /** 阅读器内用户是否手动展开了主导航（阅读时默认收缩）。 */
  readerNavExpanded: boolean
  setReaderNavExpanded: (v: boolean) => void
}

export const useShellStore = create<ShellState>((set) => ({
  readerNavExpanded: false,
  setReaderNavExpanded: (v) => set({ readerNavExpanded: v })
}))
