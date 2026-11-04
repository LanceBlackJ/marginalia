import { create } from 'zustand'

const KEY = 'pd.settings.panels'

function read(): Record<string, boolean> {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '{}') as unknown
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, boolean>) : {}
  } catch {
    return {}
  }
}

function write(open: Record<string, boolean>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(open))
  } catch {
    /* 隐私窗口或存储被禁用：只是不记住展开状态 */
  }
}

interface PanelsState {
  /** 用户明确展开或收起过的面板；没出现的面板用各自的默认状态。 */
  open: Record<string, boolean>
  set: (id: string, open: boolean) => void
}

/** 设置页各面板的展开状态，记在本机。默认全部折叠，只有“没有保存 API Key 时的连接与模型”这一个例外（由面板自己声明）。 */
export const usePanels = create<PanelsState>((set, get) => ({
  open: read(),
  set: (id, open) => {
    const next = { ...get().open, [id]: open }
    write(next)
    set({ open: next })
  }
}))
