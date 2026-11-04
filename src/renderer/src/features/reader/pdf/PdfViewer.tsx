import { selectionCollapseIsFromPanel } from '@/features/reader/selectionKeep'
import { useParaStore } from '@/features/paragraph/paraStore'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react'
import { PADDING, computeLayout, computeScale, currentPageAt, rowAtOffset, visibleRowRange, type PageSize } from './layout'
import { PageView, type SearchOverlay } from './PageView'
import type { PDFDocumentProxy } from './pdfjs'
import { pageElementFromTarget, pointInPage, readSelection } from './selection'
import { useReaderStore } from '../readerStore'
import { useMarksStore } from '../marksStore'
import { useSearchStore } from '../searchStore'
import { muteSync, syncMuted } from '../splitSync'
import { HoverTranslate, type HoverTarget } from './HoverTranslate'
import { usePrefs } from '@/stores/prefsStore'
import { useTextStore } from '@/features/text/textStore'
import { passageAtPoint } from '@/features/text/textModel'
import { buildRegions, regionAtPoint } from '../regions'
import { contentBounds } from './cropBounds'
import type { Annotation } from '@/lib/types'

/** 逐批读取每一页的真实尺寸；先用第一页尺寸占位，避免初始布局抖动。 */
function usePageSizes(doc: PDFDocumentProxy): PageSize[] {
  const [sizes, setSizes] = useState<PageSize[]>([])
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const first = await doc.getPage(1)
      const v = first.getViewport({ scale: 1 })
      let current: PageSize[] = Array.from({ length: doc.numPages }, () => ({ width: v.width, height: v.height }))
      if (cancelled) return
      setSizes(current)
      const CHUNK = 24
      for (let start = 2; start <= doc.numPages; start += CHUNK) {
        const idx = Array.from({ length: Math.min(CHUNK, doc.numPages - start + 1) }, (_, i) => start + i)
        const pages = await Promise.all(idx.map((n) => doc.getPage(n)))
        if (cancelled) return
        let changed = false
        const next = current.slice()
        pages.forEach((p, i) => {
          const vp = p.getViewport({ scale: 1 })
          const old = next[idx[i] - 1]
          if (Math.abs(old.width - vp.width) > 0.5 || Math.abs(old.height - vp.height) > 0.5) {
            next[idx[i] - 1] = { width: vp.width, height: vp.height }
            changed = true
          }
        })
        if (changed) {
          current = next
          setSizes(next)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [doc])
  return sizes
}

export function PdfViewer({ doc }: { doc: PDFDocumentProxy }) {
  const sizes = usePageSizes(doc)
  const containerRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ width: 0, height: 0 })
  const [scrollTop, setScrollTop] = useState(0)

  const zoomMode = useReaderStore((s) => s.zoomMode)
  const customScale = useReaderStore((s) => s.customScale)
  const mode = useReaderStore((s) => s.mode)
  const currentPage = useReaderStore((s) => s.currentPage)
  const jump = useReaderStore((s) => s.jump)
  const flash = useReaderStore((s) => s.flash)
  const para = useParaStore((s) => s.passage)
  const paraRects = useParaStore((s) => s.rects)

  const annotations = useMarksStore((s) => s.annotations)
  const eqList = useTextStore((s) => s.eqList)
  const figuresByCaption = useTextStore((s) => s.figuresByCaption)
  const tablesByCaption = useTextStore((s) => s.tablesByCaption)
  const regions = useMemo(() => buildRegions(eqList, figuresByCaption, tablesByCaption), [eqList, figuresByCaption, tablesByCaption])
  const hits = useSearchStore((s) => s.hits)
  const activeHit = useSearchStore((s) => s.active)

  // ---------- 容器尺寸 ----------
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect
      setBox((b) => (Math.abs(b.width - r.width) < 1 && Math.abs(b.height - r.height) < 1 ? b : { width: r.width, height: r.height }))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ---------- 缩放与布局 ----------
  const cropMargins = usePrefs((s) => s.cropMargins)
  const textItems = useTextStore((s) => s.items)
  const crop = useMemo(() => (cropMargins && sizes.length > 0 ? contentBounds(textItems, sizes[0].width) : null), [cropMargins, textItems, sizes])
  const scale = useMemo(
    () => computeScale({ zoomMode, customScale, sizes, mode, containerWidth: box.width, containerHeight: box.height, crop }),
    [zoomMode, customScale, sizes, mode, box.width, box.height, crop]
  )
  useEffect(() => {
    useReaderStore.getState().setScale(scale)
  }, [scale])

  const layout = useMemo(() => computeLayout(sizes, mode, scale), [sizes, mode, scale])
  // 裁掉白边：把内容左边界滚到视口左侧（页面本身没有被裁剪，选区与坐标都不受影响）
  useLayoutEffect(() => {
    const el = containerRef.current
    if (el && crop && zoomMode === 'fit-width' && mode !== 'double') el.scrollLeft = crop.left * scale
    else if (el && el.scrollLeft !== 0 && !crop) el.scrollLeft = 0
  }, [crop, scale, zoomMode, mode, layout.maxWidth])
  const singleRow = mode === 'single' ? layout.rows[layout.pageToRow[Math.min(currentPage, sizes.length) - 1] ?? 0] : undefined
  const innerHeight = mode === 'single' ? (singleRow?.height ?? 0) + 2 * PADDING : layout.totalHeight
  const innerWidth = Math.max(box.width, layout.maxWidth + 2 * PADDING)

  // ---------- 滚动、当前页、锚点 ----------
  const anchor = useRef<{ row: number; frac: number } | null>(null)
  const pendingJump = useRef<number | null>(null)
  const handledJump = useRef(0)
  const rafId = useRef(0)
  // 重开论文：第一次跳转恢复到上次的页内位置（pdfOffset），之后的跳转按各自的目标
  const openRestore = useRef(true)

  const onScroll = useCallback(() => {
    if (rafId.current) return
    rafId.current = requestAnimationFrame(() => {
      rafId.current = 0
      const el = containerRef.current
      if (!el) return
      setScrollTop(el.scrollTop)
      const st = useReaderStore.getState()
      if (st.mode !== 'single') {
        const lay = layoutRef.current
        const rowIdx = rowAtOffset(lay, el.scrollTop)
        const row = lay.rows[rowIdx]
        if (row) anchor.current = { row: rowIdx, frac: row.height > 0 ? (el.scrollTop - row.top) / row.height : 0 }
        if (row && pendingJump.current === null && !openRestore.current) st.setPdfOffset(Math.max(0, Math.min(1, anchor.current?.frac ?? 0)))
        if (pendingJump.current === null) {
          const pg = currentPageAt(lay, el.scrollTop, el.clientHeight)
          const changed = pg !== st.currentPage
          st.setCurrentPage(pg)
          // 并排视图的同步滚动：PDF 栏翻到新的一页，文字栏跟到同一页
          if (changed && st.readView === 'split' && usePrefs.getState().syncScroll && !syncMuted()) st.jumpToPage(pg, 'pdf')
        }
      }
      // 选区工具栏位置跟随滚动
      if (st.selection && el) {
        const info = readSelection(el)
        st.setSelection(info)
      }
    })
  }, [])
  const layoutRef = useRef(layout)
  layoutRef.current = layout

  // 布局变化（缩放/模式/窗口尺寸）后保持阅读位置
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el || mode === 'single') return
    if (pendingJump.current !== null) return
    const a = anchor.current
    if (!a) return
    const row = layout.rows[Math.min(a.row, layout.rows.length - 1)]
    if (!row) return
    el.scrollTop = Math.max(0, row.top + a.frac * row.height)
    setScrollTop(el.scrollTop)
  }, [layout, mode])

  // 跳转请求（目录/书签/证据/搜索/恢复上次位置）
  useEffect(() => {
    if (!jump || jump.id === handledJump.current) return
    const el = containerRef.current
    if (!el || layout.rows.length === 0 || box.height <= 0) {
      pendingJump.current = jump.id
      return
    }
    const rowIdx = layout.pageToRow[Math.min(jump.page, sizes.length) - 1] ?? 0
    const row = layout.rows[rowIdx]
    const store = useReaderStore.getState()
    handledJump.current = jump.id
    pendingJump.current = null
    if (jump.from === 'pdf') return // 这次跳转由 PDF 栏自己发起（并排视图）：不再滚动自己，框选高亮照常显示
    if (jump.from === 'text') muteSync()
    store.setCurrentPage(jump.page)
    const inRow = jump.bbox ? Math.max(0, jump.bbox[1] * scale - box.height * 0.3) : 0
    if (mode === 'single') {
      requestAnimationFrame(() => {
        if (containerRef.current) containerRef.current.scrollTop = jump.bbox ? PADDING + inRow : 0
      })
    } else {
      const restoring = openRestore.current && !jump.bbox && !jump.section && (jump.ord === null || jump.ord === undefined) && !jump.flash && jump.from !== 'text'
      const saved = useReaderStore.getState().pdfOffset
      openRestore.current = false
      const target = jump.bbox ? row.top + inRow : restoring && saved > 0 ? row.top + saved * row.height : Math.max(0, row.top - PADDING)
      el.scrollTop = target
      anchor.current = { row: rowIdx, frac: row.height > 0 ? (target - row.top) / row.height : 0 }
      setScrollTop(el.scrollTop)
    }
  }, [jump, layout, box.height, mode, scale, sizes.length])

  // ---------- 虚拟化 ----------
  const [first, last] = useMemo(() => {
    if (mode === 'single') return [singleRow?.index ?? 0, singleRow?.index ?? -1] as [number, number]
    return visibleRowRange(layout, scrollTop, box.height || 800, 1)
  }, [layout, scrollTop, box.height, mode, singleRow])

  // ---------- 叠加层数据 ----------
  const annByPage = useMemo(() => {
    const m = new Map<number, Annotation[]>()
    for (const a of annotations) m.set(a.page, [...(m.get(a.page) ?? []), a])
    return m
  }, [annotations])
  const hitsByPage = useMemo(() => {
    const m = new Map<number, SearchOverlay[]>()
    hits.forEach((h, i) => m.set(h.page, [...(m.get(h.page) ?? []), { bbox: h.bbox, active: i === activeHit }]))
    return m
  }, [hits, activeHit])

  // ---------- 选择 / 点击 / 键盘 / 滚轮缩放 ----------
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    let timer = 0
    const refresh = (): void => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => useReaderStore.getState().setSelection(readSelection(el)), 10)
    }
    const onSelChange = (): void => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed) {
        window.clearTimeout(timer)
        timer = window.setTimeout(() => {
          const s = window.getSelection()
          if ((!s || s.isCollapsed) && !selectionCollapseIsFromPanel()) useReaderStore.getState().setSelection(null)
        }, 120)
      }
    }
    let wheelAcc = 0
    let wheelAt = 0
    const onWheel = (e: WheelEvent): void => {
      if (!e.ctrlKey) return
      e.preventDefault()
      const now = performance.now()
      if (now - wheelAt > 250) wheelAcc = 0
      wheelAt = now
      wheelAcc += e.deltaY
      if (Math.abs(wheelAcc) >= 40) {
        useReaderStore.getState().zoomBy(wheelAcc < 0 ? 1 : -1)
        wheelAcc = 0
      }
    }
    el.addEventListener('mouseup', refresh)
    el.addEventListener('keyup', refresh)
    el.addEventListener('wheel', onWheel, { passive: false })
    document.addEventListener('selectionchange', onSelChange)
    return () => {
      window.clearTimeout(timer)
      el.removeEventListener('mouseup', refresh)
      el.removeEventListener('keyup', refresh)
      el.removeEventListener('wheel', onWheel)
      document.removeEventListener('selectionchange', onSelChange)
    }
  }, [])

  // 悬停约 0.5 秒：显示这一段的译文气泡（只在“悬停显示译文”打开且这个点真的落在某个段落里时）
  const [hover, setHover] = useState<HoverTarget | null>(null)
  const hoverTimer = useRef(0)
  const onMouseMove = (e: ReactMouseEvent): void => {
    window.clearTimeout(hoverTimer.current)
    if (!usePrefs.getState().hoverTranslate || window.getSelection()?.isCollapsed === false) return
    const cx = e.clientX
    const cy = e.clientY
    const target = e.target
    hoverTimer.current = window.setTimeout(() => {
      const pageEl = pageElementFromTarget(target)
      if (!pageEl) return setHover(null)
      const { x, y } = pointInPage(pageEl, cx, cy)
      const it = passageAtPoint(useTextStore.getState().items, Number(pageEl.dataset.page), x, y, 0)
      setHover(it && it.kind !== 'reference' ? { ord: it.ord, x: cx, y: cy } : null)
    }, 500)
  }

  const onClick = (e: ReactMouseEvent): void => {
    const sel = window.getSelection()
    const marks = useMarksStore.getState()
    if (sel && !sel.isCollapsed) return
    const pageEl = pageElementFromTarget(e.target)
    if (!pageEl) {
      marks.setActiveAnnotation(null)
      return
    }
    const page = Number(pageEl.dataset.page)
    const { x, y } = pointInPage(pageEl, e.clientX, e.clientY)
    const hit = marks.annotations.find((a) => a.page === page && a.rects.some((r) => x >= r[0] - 1 && x <= r[2] + 1 && y >= r[1] - 1 && y <= r[3] + 1))
    marks.setActiveAnnotation(hit ? { id: hit.id, x: e.clientX, y: e.clientY } : null)
    // 并排视图：点 PDF 上的一处，文字栏滚动到对应段落并高亮
    if (!hit && useReaderStore.getState().readView === 'split') {
      // 点在公式/图/表区域里：文字栏滚到对应的公式/图注/表题，PDF 上框出整块区域
      const region = regionAtPoint(regions, page, x, y)
      if (region && region.ord !== null) {
        muteSync()
        useReaderStore.getState().jumpToEvidence({ page: region.page, bbox: region.bbox, ord: region.ord, from: 'pdf' })
        return
      }
      const it = passageAtPoint(useTextStore.getState().items, page, x, y)
      if (it && it.bbox) {
        muteSync()
        useReaderStore.getState().jumpToEvidence({ page: it.page, bbox: it.bbox, ord: it.ord, from: 'pdf' })
      }
    }
  }

  const onKeyDown = (e: ReactKeyboardEvent): void => {
    const st = useReaderStore.getState()
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
    const row = layout.rows[layout.pageToRow[st.currentPage - 1] ?? 0]
    if (!row) return
    const go = (delta: number): void => {
      const target = layout.rows[Math.max(0, Math.min(layout.rows.length - 1, row.index + delta))]
      if (target && target.index !== row.index) {
        e.preventDefault()
        st.jumpToPage(target.pages[0])
      }
    }
    if (st.mode !== 'continuous') {
      if (e.key === 'PageDown' || e.key === 'ArrowRight') go(1)
      else if (e.key === 'PageUp' || e.key === 'ArrowLeft') go(-1)
    }
    if (e.key === 'Home' && st.mode !== 'continuous') {
      e.preventDefault()
      st.jumpToPage(1)
    }
  }

  return (
    <div
      className="pd-viewer"
      ref={containerRef}
      data-spot={para ? 'true' : undefined}
      data-crop={crop ? `${Math.round(crop.left)}-${Math.round(crop.right)}` : undefined}
      tabIndex={0}
      onScroll={() => {
        setHover(null)
        onScroll()
      }}
      onClick={onClick}
      onMouseMove={onMouseMove}
      onMouseLeave={() => window.clearTimeout(hoverTimer.current)}
      onKeyDown={onKeyDown}
      aria-label="PDF 阅读区域"
    >
      <HoverTranslate target={hover} onClose={() => setHover(null)} />
      <div className="pd-viewer-inner" style={{ width: innerWidth, height: innerHeight }}>
        {sizes.length > 0 &&
          last >= first &&
          layout.rows.slice(first, last + 1).map((row) => (
            <div
              key={row.index}
              className="pd-viewer-row"
              style={{
                top: mode === 'single' ? PADDING : row.top,
                height: row.height,
                width: row.width,
                left: Math.max(PADDING, (innerWidth - row.width) / 2)
              }}
            >
              {row.pages.map((p, i) => (
                <div key={p} style={{ position: 'absolute', left: row.offsets[i], top: 0 }}>
                  <PageView
                    doc={doc}
                    pageNumber={p}
                    size={sizes[p - 1]}
                    scale={scale}
                    annotations={annByPage.get(p) ?? EMPTY_ANN}
                    searchHits={hitsByPage.get(p) ?? EMPTY_HITS}
                    flash={flash && flash.page === p ? { key: flash.key, bbox: flash.bbox } : null}
                    spots={para ? (paraRects ? paraRects.filter((r) => r.page === p).map((r) => r.bbox) : para.page === p ? [para.bbox] : []) : null}
                  />
                </div>
              ))}
            </div>
          ))}
      </div>
    </div>
  )
}

const EMPTY_ANN: Annotation[] = []
const EMPTY_HITS: SearchOverlay[] = []
