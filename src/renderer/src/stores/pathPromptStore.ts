import { create } from 'zustand'

export interface PathPromptRequest {
  title: string
  label: string
  placeholder?: string
  multiline?: boolean
}

interface PathPromptState {
  request: PathPromptRequest | null
  resolver: ((value: string | null) => void) | null
  ask: (req: PathPromptRequest) => Promise<string | null>
  answer: (value: string | null) => void
}

/** Web 调试模式下替代原生文件选择对话框：让用户直接输入本机路径。 */
export const usePathPrompt = create<PathPromptState>((set, get) => ({
  request: null,
  resolver: null,
  ask: (req) =>
    new Promise((resolve) => {
      get().resolver?.(null)
      set({ request: req, resolver: resolve })
    }),
  answer: (value) => {
    get().resolver?.(value)
    set({ request: null, resolver: null })
  }
}))
