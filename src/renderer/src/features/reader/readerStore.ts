import { useTextStore } from '@/features/text/textStore'
import { activeOutlineIndex } from './outlinePos'
import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import type { BBox, Evidence, OutlineItem, PaperDetail, ReadingState, SelectionRef } from '@/lib/types'
import { SIZE_MAX, SIZE_MIN, usePrefs } from '@/stores/prefsStore'
import { nextZoomStep, type ViewMode, type ZoomMode } from './pdf/layout'
import type { SelectionInfo } from './pdf/selection'
import type { ReadView, TextView } from '@/features/text/textModel'

export type LeftTab = 'outline' | 'thumbnails' | 'bookmarks'
export type RightTab = 'ai' | 'glossary' | 'overview' | 'notes' | 'materials' | 'mindmap' | 'quiz'
const RIGHT_TABS: RightTab[] = ['ai', 'glossary', 'overview', 'notes', 'materials', 'mindmap', 'quiz']

export interface JumpRequest {
  id: number
  page: number
  bbox?: BBox | null
  /** 已知的段落序号（文字阅读器直接定位到这一段） */
  ord?: number | null
  /** 目录点击：要定位到的章节标题（文字阅读器直接滚到这个标题，PDF 阅读器只用页码） */
  section?: string | null
  flash: boolean
  /** 这次跳转由哪一栏发起（并排视图里发起的那一栏不再跟着滚动） */
  from?: 'text' | 'pdf' | null
}

export interface FlashState {
  key: number
  page: number
  bbox: BBox
  ord?: number | null
}

export const RIGHT_MIN = 320
export const RIGHT_MAX = 560
export const RIGHT_DEFAULT = 380

interface ReaderState {
  paperId: string | null
  paper: PaperDetail | null
  loadState: 'idle' | 'loading' | 'ready' | 'error'
  loadError: string | null

  numPages: number
  currentPage: number
  maxPage: number
  zoomMode: ZoomMode
  customScale: number
  /** 视图实际使用的缩放（由 PdfViewer 计算后回写） */
  scale: number
  mode: ViewMode

  leftOpen: boolean
  leftTab: LeftTab
  /** 左侧栏收成窄条 */
  leftNarrow: boolean
  setLeftNarrow: (v: boolean) => void
  /** 要在“图表”标签里展开的图（PDF 上的角标点击后设置） */
  figureFocus: { id: number; seq: number } | null
  focusFigure: (id: number) => void
  rightOpen: boolean
  rightTab: RightTab
  /** 主阅读界面：文字 / 对照 / 译文 / PDF。文字是默认的主界面。 */
  readView: ReadView
  /** 文字阅读器的字号（px）与行宽（宽 / 窄） */
  textSize: number
  textWide: boolean
  /** 这篇论文没有可提取的文字层（扫描版）：只能看 PDF */
  noText: boolean
  /** 并排视图里文字栏的呈现：文字 / 对照 / 译文 */
  splitTextView: TextView
  /** 专注模式：只剩正文，Esc 退出 */
  focus: boolean
  appearanceOpen: boolean
  helpOpen: boolean
  rightWidth: number
  searchOpen: boolean

  outline: OutlineItem[]
  outlineSource: 'detected' | 'toc' | 'none'

  jump: JumpRequest | null
  flash: FlashState | null

  /** 当前正在选中的文字（驱动浮动工具栏） */
  selection: SelectionInfo | null
  /** AI 提问使用的上下文对象（选中文字被清除后仍保留，直到用户移除） */
  contextSelection: SelectionRef | null
  composerFocusTick: number

  openPaper: (id: string) => Promise<void>
  closePaper: () => void
  patchPaper: (patch: Partial<PaperDetail>) => void
  setNumPages: (n: number) => void
  setCurrentPage: (p: number) => void
  setScale: (s: number) => void
  setZoom: (mode: ZoomMode, scale?: number) => void
  zoomBy: (dir: 1 | -1) => void
  setMode: (m: ViewMode) => void
  setLeft: (open: boolean, tab?: LeftTab) => void
  setRight: (open: boolean, tab?: RightTab) => void
  setRightWidth: (w: number) => void
  setSearchOpen: (open: boolean) => void
  /** 阅读位置记忆：文字界面最近可见的段落序号、PDF 界面当前页内的垂直偏移（0–1） */
  textAnchor: number | null
  /** 文字阅读器里按标题实际位置算出的当前小节标题（左边目录据此高亮）；PDF 视图或还没滚动时为 null */
  activeSectionTitle: string | null
  setActiveSectionTitle: (t: string | null) => void
  setTextAnchor: (ord: number | null) => void
  pdfOffset: number
  setPdfOffset: (f: number) => void
  setReadView: (v: ReadView) => void
  setTextSize: (n: number) => void
  setTextWide: (w: boolean) => void
  setNoText: (v: boolean) => void
  setSplitTextView: (v: TextView) => void
  setFocus: (v: boolean) => void
  setAppearanceOpen: (v: boolean) => void
  setHelpOpen: (v: boolean) => void
  jumpToPage: (page: number, from?: 'text' | 'pdf' | null) => void
  jumpToSection: (page: number, title: string) => void
  jumpToEvidence: (ev: Pick<Evidence, 'page' | 'bbox'> & { ord?: number | null; from?: 'text' | 'pdf' | null }) => void
  jumpToOrd: (ord: number, known?: Pick<Evidence, 'page' | 'bbox'>) => Promise<void>
  setSelection: (s: SelectionInfo | null) => void
  setContextSelection: (s: SelectionRef | null) => void
  focusComposer: () => void
  currentSectionTitle: () => string | null
}

let jumpSeq = 0
let flashTimer: number | undefined

function zoomFromPrefs(): { zoomMode: ZoomMode; customScale: number } {
  const z = usePrefs.getState().pdfDefaultZoom
  if (z === 'fit-width' || z === 'fit-page') return { zoomMode: z, customScale: 1 }
  return { zoomMode: 'custom', customScale: Number(z) / 100 }
}

export const useReaderStore = create<ReaderState>((set, get) => ({
  paperId: null,
  paper: null,
  loadState: 'idle',
  loadError: null,

  numPages: 0,
  currentPage: 1,
  maxPage: 1,
  zoomMode: 'fit-width',
  customScale: 1,
  scale: 1,
  mode: 'continuous',

  leftOpen: true,
  leftNarrow: false,
  textAnchor: null,
  activeSectionTitle: null,
  setActiveSectionTitle: (t) => set((s) => (s.activeSectionTitle === t ? s : { activeSectionTitle: t })),
  setTextAnchor: (ord) => set((s) => (s.textAnchor === ord ? s : { textAnchor: ord })),
  pdfOffset: 0,
  setPdfOffset: (f) => set((s) => (Math.abs(s.pdfOffset - f) < 0.01 ? s : { pdfOffset: f })),
  setLeftNarrow: (v) => set({ leftNarrow: v, leftOpen: true }),
  figureFocus: null,
  focusFigure: (id) => {
    set((s) => ({ figureFocus: { id, seq: (s.figureFocus?.seq ?? 0) + 1 } }))
  },
  leftTab: 'outline',
  rightOpen: false,
  rightTab: 'ai',
  readView: 'text',
  textSize: usePrefs.getState().readSize,
  textWide: usePrefs.getState().measure === 'wide' || usePrefs.getState().measure === 'full',
  noText: false,
  splitTextView: 'text',
  focus: false,
  appearanceOpen: false,
  helpOpen: false,
  rightWidth: RIGHT_DEFAULT,
  searchOpen: false,

  outline: [],
  outlineSource: 'none',

  jump: null,
  flash: null,
  selection: null,
  contextSelection: null,
  composerFocusTick: 0,

  openPaper: async (id) => {
    window.clearTimeout(flashTimer)
    set({
      paperId: id,
      paper: null,
      loadState: 'loading',
      loadError: null,
      numPages: 0,
      currentPage: 1,
      maxPage: 1,
      jump: null,
      flash: null,
      selection: null,
      contextSelection: null,
      outline: [],
      searchOpen: false
    })
    try {
      const paper = await api.papers.get(id)
      if (get().paperId !== id) return
      const rs: ReadingState = paper.reading_state ?? {}
      const prefs = usePrefs.getState()
      const defaults = zoomFromPrefs()
      const startPage = Math.max(1, paper.reading_page || 1)
      set({
        paper,
        loadState: 'ready',
        currentPage: startPage,
        maxPage: Math.max(startPage, rs.maxPage ?? 1),
        zoomMode: rs.zoomMode ?? defaults.zoomMode,
        customScale: rs.scale ?? defaults.customScale,
        mode: rs.mode ?? prefs.defaultReadMode,
        leftOpen: rs.leftOpen ?? true,
        leftNarrow: rs.leftNarrow ?? false,
        leftTab: rs.leftTab ?? 'outline',
        // 以提取出的文字为主：右侧面板默认收起。没有 readView 的旧阅读状态来自文字阅读器之前，
        // 它里面的 rightOpen 不代表用户的选择，这里按新默认处理。
        rightOpen: rs.readView === undefined ? false : (rs.rightOpen ?? false),
        rightTab: rs.rightTab && RIGHT_TABS.includes(rs.rightTab as RightTab) ? (rs.rightTab as RightTab) : 'ai',
        readView: rs.readView ?? 'text',
        textSize: usePrefs.getState().readSize,
        textWide: usePrefs.getState().measure === 'wide' || usePrefs.getState().measure === 'full',
        noText: false,
        numPages: paper.page_count || 0,
        rightWidth: Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, rs.rightWidth ?? RIGHT_DEFAULT)),
        textAnchor: rs.textAnchor ?? null,
        pdfOffset: rs.pdfOffset ?? 0,
        // 重开论文回到上次读到的位置：文字界面精确到段落，PDF 界面（见 PdfViewer）恢复页内偏移
        jump: { id: ++jumpSeq, page: startPage, ord: (rs.readView ?? 'text') !== 'pdf' ? (rs.textAnchor ?? null) : null, flash: false }
      })
      api.papers.patch(id, { touch: true }).catch(() => undefined)
      api.papers
        .outline(id)
        .then((o) => get().paperId === id && set({ outline: o.items, outlineSource: o.source }))
        .catch(() => undefined)
    } catch (err) {
      if (get().paperId === id) set({ loadState: 'error', loadError: errorMessage(err) })
    }
  },

  closePaper: () => {
    window.clearTimeout(flashTimer)
    set({ paperId: null, paper: null, loadState: 'idle', selection: null, jump: null, flash: null, activeSectionTitle: null })
  },

  patchPaper: (patch) => set((s) => (s.paper ? { paper: { ...s.paper, ...patch } } : s)),

  setNumPages: (n) => set({ numPages: n }),
  setCurrentPage: (p) => {
    const page = Math.max(1, Math.min(p, get().numPages || p))
    set((s) => ({ currentPage: page, maxPage: Math.max(s.maxPage, page) }))
  },
  setScale: (scale) => set((s) => (Math.abs(s.scale - scale) < 0.0005 ? s : { scale })),
  setZoom: (zoomMode, scale) => set((s) => ({ zoomMode, customScale: scale ?? s.customScale })),
  zoomBy: (dir) => {
    const next = nextZoomStep(get().scale, dir)
    set({ zoomMode: 'custom', customScale: next })
  },
  setMode: (mode) => {
    set({ mode })
    // 切换模式后回到当前页，避免位置漂移
    get().jumpToPage(get().currentPage)
  },
  setLeft: (open, tab) => set((s) => ({ leftOpen: open, leftTab: tab ?? s.leftTab })),
  setRight: (open, tab) =>
    set((s) => {
      // 窗口较窄时打开右侧面板，自动收起左侧栏，避免挤占阅读区域
      const narrow = typeof window !== 'undefined' && window.innerWidth < 1280
      return { rightOpen: open, rightTab: tab ?? s.rightTab, leftOpen: open && narrow ? false : s.leftOpen }
    }),
  setRightWidth: (w) => set({ rightWidth: Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, Math.round(w))) }),
  setSearchOpen: (open) => set({ searchOpen: open }),
  setReadView: (readView) => {
    set({ readView })
    // 切换界面后回到当前页，避免位置漂移
    get().jumpToPage(get().currentPage)
  },
  setTextSize: (n) => {
    const v = Math.min(SIZE_MAX, Math.max(SIZE_MIN, Math.round(n)))
    usePrefs.getState().set({ readSize: v })
    set({ textSize: v })
  },
  setTextWide: (textWide) => {
    usePrefs.getState().set({ measure: textWide ? 'wide' : 'medium' })
    set({ textWide })
  },
  setNoText: (noText) => set({ noText }),
  setSplitTextView: (splitTextView) => set({ splitTextView }),
  setFocus: (focus) => set({ focus }),
  setAppearanceOpen: (appearanceOpen) => set({ appearanceOpen }),
  setHelpOpen: (helpOpen) => set({ helpOpen }),

  jumpToPage: (page, from) => {
    const p = Math.max(1, Math.min(page, get().numPages || page))
    set({ jump: { id: ++jumpSeq, page: p, flash: false, from: from ?? null } })
  },

  jumpToSection: (page, title) => {
    const p = Math.max(1, Math.min(page, get().numPages || page))
    set({ jump: { id: ++jumpSeq, page: p, section: title, flash: false } })
  },

  jumpToEvidence: (ev) => {
    const key = ++jumpSeq
    window.clearTimeout(flashTimer)
    set({ jump: { id: key, page: ev.page, bbox: ev.bbox, ord: ev.ord ?? null, flash: true, from: ev.from ?? null }, flash: { key, page: ev.page, bbox: ev.bbox, ord: ev.ord ?? null } })
    flashTimer = window.setTimeout(() => {
      if (get().flash?.key === key) set({ flash: null })
    }, 2800)
  },

  jumpToOrd: async (ord, known) => {
    if (known) {
      get().jumpToEvidence({ ...known, ord })
      return
    }
    const id = get().paperId
    if (!id) return
    try {
      const p = await api.papers.passage(id, ord)
      if (get().paperId === id) get().jumpToEvidence({ page: p.page, bbox: p.bbox, ord })
    } catch {
      /* 段落不存在：保持不动 */
    }
  },

  setSelection: (selection) => set({ selection }),
  setContextSelection: (contextSelection) => set({ contextSelection }),
  focusComposer: () => set((s) => ({ composerFocusTick: s.composerFocusTick + 1 })),

  currentSectionTitle: () => {
    const { outline, currentPage, textAnchor, activeSectionTitle } = get()
    const idx = activeOutlineIndex(outline, useTextStore.getState().items, textAnchor, currentPage, activeSectionTitle)
    return idx >= 0 ? outline[idx].title : null
  }
}))

// 阅读外观面板改了字号/行宽后，阅读器里的状态跟着变（偏好是唯一的来源）
usePrefs.subscribe((p) => {
  const wide = p.measure === 'wide' || p.measure === 'full'
  const cur = useReaderStore.getState()
  if (cur.textSize !== p.readSize || cur.textWide !== wide) useReaderStore.setState({ textSize: p.readSize, textWide: wide })
})
