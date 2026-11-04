import type { KeyboardEvent, ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui'
import { NOTE_PLACEHOLDER } from './NoteBody'
import { LiveNoteEditor } from './LiveNote'
import './sticky.css'

/** 网格里的第一格：点一下就地展开成一张可以写的便签，不再放一个常驻的大输入框。 */
export function NewStickyTile({ onClick, label = '写一张便签' }: { onClick: () => void; label?: string }) {
  return (
    <button type="button" className="pd-sticky-new" onClick={onClick}>
      <Plus size={18} />
      <span>{label}</span>
    </button>
  )
}

/**
 * 一张正在写的便签：纸色底，没有“编辑 / 预览”之分——写完一段（空一行）、写完一个公式就自动渲染成最终的样子，点哪一块就回到哪一块的源码继续改。
 * Ctrl/⌘ + Enter 保存，Esc 取消。新建和修改共用。
 */
export function StickyEditor({
  value,
  onChange,
  onSave,
  onCancel,
  saving = false,
  saveLabel = '保存',
  header,
  placeholder = NOTE_PLACEHOLDER,
  canSaveEmpty = false
}: {
  value: string
  onChange: (v: string) => void
  onSave: () => void
  onCancel: () => void
  saving?: boolean
  saveLabel?: string
  /** 便签上方的一行小字（页码、引用的原文等） */
  header?: ReactNode
  placeholder?: string
  /** 没写字也能保存（划词记笔记时，引用的原文本身就是内容） */
  canSaveEmpty?: boolean
}) {
  const onKey = (e: KeyboardEvent): void => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      if ((value.trim() || canSaveEmpty) && !saving) onSave()
    }
  }

  return (
    <article className="pd-sticky pd-sticky--edit" onKeyDown={onKey}>
      {header && <div className="pd-sticky-head">{header}</div>}
      <LiveNoteEditor value={value} onChange={onChange} placeholder={placeholder} />
      <div className="pd-sticky-bar">
        <span className="pd-spacer" />
        <Button size="sm" variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button size="sm" variant="primary" loading={saving} disabled={!value.trim() && !canSaveEmpty} onClick={onSave} title="Ctrl + Enter 保存">
          {saveLabel}
        </Button>
      </div>
    </article>
  )
}
