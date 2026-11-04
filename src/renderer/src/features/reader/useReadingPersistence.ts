import { useEffect } from 'react'
import { api } from '@/lib/api'
import type { ReadingState } from '@/lib/types'
import { useReaderStore } from './readerStore'

/** 阅读状态自动保存：页码、缩放、模式、面板状态变化后防抖 PATCH；卸载时立即写入。 */
export function useReadingPersistence(paperId: string | undefined, ready: boolean): void {
  useEffect(() => {
    if (!paperId || !ready) return
    let timer: number | undefined
    let dirty = false

    const flush = (): void => {
      window.clearTimeout(timer)
      if (!dirty) return
      dirty = false
      const s = useReaderStore.getState()
      if (s.paperId !== paperId || s.numPages === 0) return
      const reading_state: ReadingState = {
        zoomMode: s.zoomMode,
        scale: s.customScale,
        mode: s.mode,
        leftOpen: s.leftOpen,
        leftNarrow: s.leftNarrow,
        textAnchor: s.textAnchor ?? undefined,
        pdfOffset: s.pdfOffset,
        leftTab: s.leftTab,
        rightOpen: s.rightOpen,
        rightTab: s.rightTab,
        rightWidth: s.rightWidth,
        readView: s.readView,
        textSize: s.textSize,
        textWide: s.textWide,
        maxPage: s.maxPage
      }
      api.papers
        .patch(paperId, { reading_page: s.currentPage, read_progress: Math.min(1, s.maxPage / s.numPages), reading_state })
        .catch(() => undefined)
    }

    const unsub = useReaderStore.subscribe((s, prev) => {
      if (
        s.currentPage !== prev.currentPage ||
        s.zoomMode !== prev.zoomMode ||
        s.customScale !== prev.customScale ||
        s.mode !== prev.mode ||
        s.leftOpen !== prev.leftOpen ||
        s.leftNarrow !== prev.leftNarrow ||
        s.textAnchor !== prev.textAnchor ||
        s.pdfOffset !== prev.pdfOffset ||
        s.leftTab !== prev.leftTab ||
        s.rightOpen !== prev.rightOpen ||
        s.rightTab !== prev.rightTab ||
        s.rightWidth !== prev.rightWidth ||
        s.readView !== prev.readView ||
        s.textSize !== prev.textSize ||
        s.textWide !== prev.textWide
      ) {
        dirty = true
        window.clearTimeout(timer)
        timer = window.setTimeout(flush, 800)
      }
    })
    return () => {
      unsub()
      flush()
    }
  }, [paperId, ready])
}
