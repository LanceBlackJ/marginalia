import type { ReactNode } from 'react'
import { BookA, ListChecks, Network, NotebookPen, Paperclip, ScrollText, Sparkles } from 'lucide-react'
import { Tooltip } from '@/components/ui'
import { useNotesStore } from '@/features/notes/notesStore'
import { useMaterialsStore } from '@/features/materials/materialsStore'
import { useReaderStore, type RightTab } from './readerStore'

const ICON = 17

interface RailEntry {
  key: RightTab
  label: string
  icon: ReactNode
  count?: number
}

/**
 * 阅读区最右边的一条竖向图标栏（和最左边的应用导航同一个样子）：点哪个，右边就出现哪个面板；
 * 再点一下正在显示的那个，面板收起。名字放在鼠标悬停的提示里，不占位置。
 */
export function RightRail() {
  const open = useReaderStore((s) => s.rightOpen)
  const tab = useReaderStore((s) => s.rightTab)
  const setRight = useReaderStore((s) => s.setRight)
  const noteCount = useNotesStore((s) => s.notes.length)
  const isSupplement = useReaderStore((s) => s.paper?.role === 'supplement')
  const materialCount = useMaterialsStore((s) => s.items.length)

  const entries: RailEntry[] = [
    { key: 'ai', label: 'AI 提问', icon: <Sparkles size={ICON} /> },
    { key: 'glossary', label: '术语', icon: <BookA size={ICON} /> },
    { key: 'overview', label: '概览', icon: <ScrollText size={ICON} /> },
    { key: 'notes', label: '笔记', icon: <NotebookPen size={ICON} />, count: noteCount },
    { key: 'mindmap', label: '导图', icon: <Network size={ICON} /> },
    { key: 'quiz', label: '自测', icon: <ListChecks size={ICON} /> },
    // 补充材料属于主论文；补充材料自己的阅读器里不再套娃
    ...(isSupplement ? [] : [{ key: 'materials' as RightTab, label: '材料', icon: <Paperclip size={ICON} />, count: materialCount }])
  ]
  const active: RightTab = tab === 'materials' && isSupplement ? 'ai' : tab

  return (
    <nav className="pd-rrail" aria-label="阅读辅助">
      {entries.map((e) => {
        const selected = open && active === e.key
        const name = e.count ? `${e.label} ${e.count}` : e.label
        return (
          <Tooltip key={e.key} content={name}>
            <button
              type="button"
              className="pd-rrail-item"
              data-active={selected}
              aria-label={name}
              aria-pressed={selected}
              onClick={() => (selected ? setRight(false) : setRight(true, e.key))}
            >
              {e.icon}
              {!!e.count && <span className="pd-rrail-badge">{e.count > 99 ? '99+' : e.count}</span>}
            </button>
          </Tooltip>
        )
      })}
    </nav>
  )
}
