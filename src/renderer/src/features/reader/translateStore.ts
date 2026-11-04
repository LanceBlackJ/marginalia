import { create } from 'zustand'
import { ApiError, api, errorMessage } from '@/lib/api'
import type { SelectionInfo } from './pdf/selection'
import { useReaderStore } from './readerStore'

const MAX_CHARS = 6000

interface Anchor {
  left: number
  /** 在选区下方时用 top，在上方时用 bottom（视口坐标） */
  top?: number
  bottom?: number
}

interface TranslateState {
  open: boolean
  anchor: Anchor
  page: number
  source: string
  truncated: boolean
  translation: string
  status: 'idle' | 'streaming' | 'done' | 'error'
  model: string | null
  cached: boolean
  error: { code: string; message: string } | null

  start: (sel: Pick<SelectionInfo, 'text' | 'page' | 'clientRect'>, force?: boolean) => Promise<void>
  retranslate: () => Promise<void>
  close: () => void
}

let controller: AbortController | null = null

function anchorFor(rect: SelectionInfo['clientRect']): Anchor {
  const left = Math.min(Math.max((rect.left + rect.right) / 2, 220), window.innerWidth - 220)
  const below = rect.bottom + 10
  // 下方空间不足时放到选区上方
  if (below + 280 > window.innerHeight) return { left, bottom: Math.max(8, window.innerHeight - rect.top + 10) }
  return { left, top: below }
}

export const useTranslateStore = create<TranslateState>((set, get) => ({
  open: false,
  anchor: { left: 0, top: 0 },
  page: 1,
  source: '',
  truncated: false,
  translation: '',
  status: 'idle',
  model: null,
  cached: false,
  error: null,

  start: async (sel, force = false) => {
    const paperId = useReaderStore.getState().paperId
    if (!paperId) return
    controller?.abort()
    const ctrl = new AbortController()
    controller = ctrl
    const truncated = sel.text.length > MAX_CHARS
    const text = truncated ? sel.text.slice(0, MAX_CHARS) : sel.text
    set({
      open: true,
      anchor: force ? get().anchor : anchorFor(sel.clientRect),
      page: sel.page,
      source: sel.text,
      truncated,
      translation: '',
      status: 'streaming',
      model: null,
      cached: false,
      error: null
    })
    let done = false
    try {
      await api.ai.translate(
        { paper_id: paperId, text, page: sel.page, force },
        (event, data) => {
          const d = data as Record<string, unknown>
          if (event === 'meta') set({ model: String(d.model ?? ''), cached: Boolean(d.cached) })
          else if (event === 'delta') set((s) => ({ translation: s.translation + String(d.text ?? '') }))
          else if (event === 'done') {
            done = true
            set({ translation: String(d.translation ?? get().translation), status: 'done', model: String(d.model ?? get().model ?? ''), cached: Boolean(d.cached) })
          } else if (event === 'error') {
            done = true
            set({ status: 'error', error: { code: String(d.code ?? 'internal'), message: String(d.message ?? '翻译失败') } })
          }
        },
        ctrl.signal
      )
      if (!done && !ctrl.signal.aborted) set({ status: 'error', error: { code: 'network', message: '连接意外中断，翻译不完整。' } })
    } catch (err) {
      if (ctrl.signal.aborted) return
      set({ status: 'error', error: { code: err instanceof ApiError ? err.code : 'internal', message: errorMessage(err) } })
    }
  },

  retranslate: async () => {
    const s = get()
    await s.start({ text: s.source, page: s.page, clientRect: { left: 0, top: 0, right: 0, bottom: 0 } }, true)
  },

  close: () => {
    controller?.abort()
    set({ open: false, status: 'idle' })
  }
}))
