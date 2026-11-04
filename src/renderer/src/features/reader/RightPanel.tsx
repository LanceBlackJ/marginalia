import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { X } from 'lucide-react'
import { IconButton } from '@/components/ui'
import { ChainSwitcher } from '@/features/insight/ChainSwitcher'
import { HistoryMenu } from '@/features/insight/HistoryMenu'
import { InsightPanel } from '@/features/insight/InsightPanel'
import { NotesPanel } from '@/features/notes/NotesPanel'
import { GlossaryPanel } from '@/features/glossary/GlossaryPanel'
import { OverviewPanel } from '@/features/overview/OverviewPanel'
import { useNotesStore } from '@/features/notes/notesStore'
import { QuizPanel } from '@/features/quiz/QuizPanel'
import { MindMapPanel } from '@/features/mindmap/MindMapPanel'
import { MaterialsPanel } from '@/features/materials/MaterialsPanel'
import { useMaterialsStore } from '@/features/materials/materialsStore'
import { ContextBar } from './ContextBar'
import { useReaderStore, type RightTab } from './readerStore'

export function RightPanel() {
  const width = useReaderStore((s) => s.rightWidth)
  const tab = useReaderStore((s) => s.rightTab)
  const setRight = useReaderStore((s) => s.setRight)
  const setRightWidth = useReaderStore((s) => s.setRightWidth)
  const noteCount = useNotesStore((s) => s.notes.length)
  const isSupplement = useReaderStore((s) => s.paper?.role === 'supplement')
  const materialCount = useMaterialsStore((s) => s.items.length)
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

  // 纯文字短标签：全部放得进面板宽度（不再横向滚动、不会有标签被挤到看不见）
  const tabs: Array<{ key: RightTab; label: string }> = [
    { key: 'ai', label: 'AI' },
    { key: 'glossary', label: '术语' },
    { key: 'overview', label: '概览' },
    { key: 'notes', label: noteCount > 0 ? `笔记 ${noteCount}` : '笔记' },
    { key: 'mindmap', label: '导图' },
    { key: 'quiz', label: '自测' },
    // 补充材料属于主论文；补充材料自己的阅读器里不再套娃
    ...(isSupplement ? [] : [{ key: 'materials' as RightTab, label: materialCount > 0 ? `材料 ${materialCount}` : '材料' }])
  ]
  const activeTab: RightTab = tab === 'materials' && isSupplement ? 'ai' : tab
  const tabsRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // 标签栏可横向滚动：切换标签后把选中的标签滚进可见范围
    tabsRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ inline: 'nearest', block: 'nearest' })
  }, [activeTab])

  return (
    <aside className="pd-rpanel" style={{ width }} aria-label="AI 面板">
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
      <div className="pd-rpanel-tabs" role="tablist" ref={tabsRef}>
        {tabs.map((t) => (
          <button key={t.key} type="button" role="tab" className="pd-rtab" aria-selected={activeTab === t.key} onClick={() => setRight(true, t.key)}>
            {t.label}
          </button>
        ))}
        <span className="pd-spacer" />
        <IconButton icon={<X size={16} />} label="关闭面板" size="sm" onClick={() => setRight(false)} />
      </div>
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
    </aside>
  )
}
