import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { BookMarked, MoreHorizontal, Pencil, Plus, Trash2 } from 'lucide-react'
import { IconButton, Menu } from '@/components/ui'
import type { ReviewProject } from '@/lib/types.library'

export interface ReviewRailProps {
  projects: ReviewProject[]
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: (name: string) => Promise<boolean>
  onRename: (project: ReviewProject, name: string) => Promise<boolean>
  onEdit: (project: ReviewProject) => void
  onDelete: (project: ReviewProject) => void
}

function InlineReviewName({ initial = '', onSubmit, onCancel }: { initial?: string; onSubmit: (name: string) => Promise<boolean>; onCancel: () => void }) {
  const [value, setValue] = useState(initial)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { ref.current?.focus(); ref.current?.select() }, [])
  const submit = async (): Promise<void> => {
    const name = value.trim()
    if (!name || busy) return onCancel()
    setBusy(true)
    const ok = await onSubmit(name)
    setBusy(false)
    if (ok) onCancel()
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) void submit()
    if (e.key === 'Escape') onCancel()
  }
  return <input ref={ref} className="pd-frail-input" value={value} placeholder="综述名称" maxLength={120} onChange={(e) => setValue(e.target.value)} onKeyDown={onKey} onBlur={() => void submit()} aria-label="综述名称" />
}

export function ReviewRail(p: ReviewRailProps) {
  const [creating, setCreating] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  return (
    <>
      <div className="pd-frail-head">
        <span>综述</span>
        <IconButton icon={<Plus size={14} />} label="新建综述" size="sm" onClick={() => setCreating(true)} />
      </div>
      <div className="pd-frail-group">
        {creating && <div className="pd-frail-item"><BookMarked size={14} /><InlineReviewName onSubmit={p.onCreate} onCancel={() => setCreating(false)} /></div>}
        {p.projects.map((project) => (
          <div key={project.id} className={`pd-frail-item${p.activeId === project.id ? ' is-active' : ''}`} onClick={() => p.onSelect(project.id)}>
            <BookMarked size={14} />
            {renaming === project.id ? <InlineReviewName initial={project.name} onSubmit={(name) => p.onRename(project, name)} onCancel={() => setRenaming(null)} /> : <span className="pd-frail-name" title={project.description || project.name}>{project.name}</span>}
            <span className="pd-frail-count">{project.paper_count}</span>
            <span className="pd-frail-more" onClick={(e) => e.stopPropagation()}>
              <Menu
                align="end"
                trigger={({ toggle, open }) => <IconButton icon={<MoreHorizontal size={14} />} label="综述操作" size="sm" selected={open} onClick={toggle} />}
                items={[
                  { key: 'rename', label: '重命名', icon: <Pencil size={15} />, onSelect: () => setRenaming(project.id) },
                  { key: 'edit', label: '编辑简介', icon: <Pencil size={15} />, onSelect: () => p.onEdit(project) },
                  { key: 'delete', label: '删除综述…', icon: <Trash2 size={15} />, danger: true, onSelect: () => p.onDelete(project) }
                ]}
              />
            </span>
          </div>
        ))}
        {!creating && p.projects.length === 0 && <button type="button" className="pd-frail-empty" onClick={() => setCreating(true)}>新建一个综述，开始独立整理文献</button>}
      </div>
    </>
  )
}
