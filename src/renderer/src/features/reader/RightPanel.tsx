import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { PANEL_SCALE_MAX, PANEL_SCALE_MIN, usePrefs } from '@/stores/prefsStore'
import { ChainSwitcher } from '@/features/insight/ChainSwitcher'
import { HistoryMenu } from '@/features/insight/HistoryMenu'
import { InsightPanel } from '@/features/insight/InsightPanel'
import { NotesPanel } from '@/features/notes/NotesPanel'
import { GlossaryPanel } from '@/features/glossary/GlossaryPanel'
import { OverviewPanel } from '@/features/overview/OverviewPanel'
import { QuizPanel } from '@/features/quiz/QuizPanel'
import { MindMapPanel } from '@/features/mindmap/MindMapPanel'
import { MaterialsPanel } from '@/features/materials/MaterialsPanel'
import { ContextBar } from './ContextBar'
import { onCtrlWheel } from './ctrlWheel'
import { useReaderStore, type RightTab } from './readerStore'

export function RightPanel() {
  const width = useReaderStore((s) => s.rightWidth)
  const tab = useReaderStore((s) => s.rightTab)
  const setRightWidth = useReaderStore((s) => s.setRightWidth)
  const isSupplement = useReaderStore((s) => s.paper?.role === 'supplement')
  const scale = usePrefs((s) => s.panelScale)
  const asideRef = useRef<HTMLElement>(null)
  const [dragging, setDragging] = useState(false)
  const startRef = useRef<{ x: number; w: number } | null>(null)

  const onDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    e.currentTarget.setPointerCapture(e.pointerId)
    startRef.current = { x: e.clientX, w: width }
    setDragging(true)
  }
  const onMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (!startRef.current) return
    setRightWidth(startRef.current.w + (startRef.current.x - e.clientX))
  }
  const onUp = (e: ReactPointerEvent<HTMLDivElement>): void => {
    startRef.current = null
    setDragging(false)
    e.currentTarget.releasePointerCapture(e.pointerId)
  }

  // 面板里是哪个，由最右边的图标栏（RightRail）选；补充材料自己的阅读器里没有“材料”
  const activeTab: RightTab = tab === 'materials' && isSupplement ? 'ai' : tab

  // 在面板上按 Ctrl+鼠标滚轮：整个面板里的字一起放大/缩小（每格 10%，80%–160%），设置会记住
  useEffect(() => {
    const el = asideRef.current
    if (!el) return
    return onCtrlWheel(el, (dir) => {
      const cur = usePrefs.getState().panelScale
      const next = Math.round(Math.min(PANEL_SCALE_MAX, Math.max(PANEL_SCALE_MIN, cur + dir * 0.1)) * 10) / 10
      if (next !== cur) usePrefs.getState().set({ panelScale: next })
    })
  }, [])

  return (
    <aside className="pd-rpanel" ref={asideRef} style={{ width }} aria-label="AI 面板">
      <div
        className="pd-rpanel-resizer"
        data-dragging={dragging}
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整面板宽度"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      />
      <div className="pd-rpanel-zoom" style={{ zoom: scale }}>
      <ContextBar>
        {activeTab === 'ai' && (
          <>
            <ChainSwitcher />
            <HistoryMenu />
          </>
        )}
      </ContextBar>
      {activeTab === 'ai' && <InsightPanel />}
      {activeTab === 'glossary' && <GlossaryPanel />}
      {activeTab === 'overview' && <OverviewPanel />}
      {activeTab === 'notes' && <NotesPanel />}
      {activeTab === 'materials' && <MaterialsPanel />}
      {activeTab === 'mindmap' && <MindMapPanel />}
      {activeTab === 'quiz' && <QuizPanel />}
      </div>
    </aside>
  )
}
