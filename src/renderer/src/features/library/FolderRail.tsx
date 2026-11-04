import { useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { ChevronDown, ChevronRight, FolderClosed, FolderOpen, Inbox, Layers, MoreHorizontal, Plus, Star, Clock, Filter, Pencil, Trash2, FolderPlus, X, Info } from 'lucide-react'
import { IconButton, Menu } from '@/components/ui'
import type { FolderItem, FolderList, FolderNode, SmartView } from '@/lib/types.library'
import { buildFolderTree, canNestFolder, DRAG_FOLDER, DRAG_PAPERS, flattenTree, reorderIds } from './filterModel'

type View = 'all' | 'recent' | 'favorite'
type DropPos = 'inside' | 'before' | 'after'

export interface FolderRailProps {
  folders: FolderList
  views: SmartView[]
  view: View
  /** 当前选中的文件夹：id、'none'（未分类）或 undefined（不限） */
  folderId: string | undefined
  activeViewId: string | null
  onSelectView: (v: View) => void
  onSelectFolder: (id: string | undefined) => void
  onApplyView: (v: SmartView) => void
  onDeleteView: (v: SmartView) => void
  onDropPapers: (folderId: string | null, paperIds: string[]) => void
  onCreate: (name: string, parentId: string | null) => Promise<boolean>
  onRename: (f: FolderItem, name: string) => Promise<boolean>
  onDelete: (f: FolderItem) => void
  onMoveFolder: (id: string, parentId: string | null) => void
  onReorder: (parentId: string | null, ids: string[]) => void
}

const EXPANDED_KEY = 'paperdesk.folders.expanded'

function loadExpanded(): Set<string> {
  try {
    const raw = sessionStorage.getItem(EXPANDED_KEY)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

function InlineName({ initial, placeholder, onSubmit, onCancel }: { initial: string; placeholder: string; onSubmit: (name: string) => Promise<boolean>; onCancel: () => void }) {
  const [text, setText] = useState(initial)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const submit = async (): Promise<void> => {
    const name = text.trim()
    if (!name || busy) return onCancel()
    if (name === initial) return onCancel()
    setBusy(true)
    const ok = await onSubmit(name)
    setBusy(false)
    if (ok) onCancel()
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) void submit()
    if (e.key === 'Escape') onCancel()
  }
  return <input ref={ref} className="pd-frail-input" value={text} placeholder={placeholder} maxLength={60} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onBlur={() => void submit()} aria-label="文件夹名称" />
}

/**
 * 文库左侧：系统视图、文件夹树（可拖放）、智能视图。
 * 拖论文到文件夹 = 移动；拖文件夹到另一个文件夹中间 = 放进去，拖到上下边缘 = 调整顺序。
 */
export function FolderRail(p: FolderRailProps) {
  const tree = useMemo(() => buildFolderTree(p.folders.items), [p.folders.items])
  const [expanded, setExpanded] = useState<Set<string>>(loadExpanded)
  const [creating, setCreating] = useState<string | null | undefined>(undefined) // undefined=不在新建；null=最上层；id=子文件夹
  const [renaming, setRenaming] = useState<string | null>(null)
  const [drop, setDrop] = useState<{ id: string; pos: DropPos } | null>(null)
  const [help, setHelp] = useState(false)

  useEffect(() => {
    try {
      sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...expanded]))
    } catch {
      /* 存不下不影响使用 */
    }
  }, [expanded])

  // 选中的文件夹在折叠的父级里时，自动展开到它
  useEffect(() => {
    if (!p.folderId || p.folderId === 'none') return
    const need: string[] = []
    let cur = p.folders.items.find((f) => f.id === p.folderId)?.parent_id ?? null
    while (cur) {
      need.push(cur)
      cur = p.folders.items.find((f) => f.id === cur)?.parent_id ?? null
    }
    if (need.some((id) => !expanded.has(id))) setExpanded((s) => new Set([...s, ...need]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.folderId, p.folders.items])

  const toggle = (id: string): void =>
    setExpanded((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const rows = useMemo(() => flattenTree(tree, expanded), [tree, expanded])

  const hasType = (e: DragEvent, type: string): boolean => Array.from(e.dataTransfer.types).includes(type)

  const dropPos = (e: DragEvent<HTMLElement>, folderDrag: boolean): DropPos => {
    if (!folderDrag) return 'inside'
    const r = e.currentTarget.getBoundingClientRect()
    const y = (e.clientY - r.top) / r.height
    return y < 0.28 ? 'before' : y > 0.72 ? 'after' : 'inside'
  }

  const onDragOverFolder = (e: DragEvent<HTMLElement>, f: FolderNode): void => {
    const papers = hasType(e, DRAG_PAPERS)
    const folder = hasType(e, DRAG_FOLDER)
    if (!papers && !folder) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    const pos = dropPos(e, folder)
    setDrop((d) => (d && d.id === f.id && d.pos === pos ? d : { id: f.id, pos }))
  }

  const onDropFolder = (e: DragEvent<HTMLElement>, f: FolderNode): void => {
    e.preventDefault()
    const pos = dropPos(e, hasType(e, DRAG_FOLDER))
    setDrop(null)
    const paperRaw = e.dataTransfer.getData(DRAG_PAPERS)
    if (paperRaw) {
      p.onDropPapers(f.id, JSON.parse(paperRaw) as string[])
      return
    }
    const dragId = e.dataTransfer.getData(DRAG_FOLDER)
    if (!dragId || dragId === f.id) return
    if (pos === 'inside') {
      if (canNestFolder(p.folders.items, dragId, f.id)) {
        p.onMoveFolder(dragId, f.id)
        setExpanded((s) => new Set([...s, f.id]))
      }
      return
    }
    // 放到 f 之前/之后：先放进 f 的父级，再按顺序重排
    const parent = f.parent_id
    if (!canNestFolder(p.folders.items, dragId, parent)) return
    const siblings = p.folders.items.filter((x) => x.parent_id === parent).sort((a, b) => a.sort - b.sort).map((x) => x.id)
    const dragged = p.folders.items.find((x) => x.id === dragId)
    const target = pos === 'before' ? f.id : (siblings[siblings.indexOf(f.id) + 1] ?? null)
    if (dragged && dragged.parent_id !== parent) {
      // 跨级：先移动，再由 onMoveFolder 的调用方刷新后排序——这里把顺序一并交给 onReorder（后端 reorder 要求恰好是该级全部文件夹）
      p.onMoveFolder(dragId, parent)
      return
    }
    p.onReorder(parent, reorderIds(siblings, dragId, target))
  }

  const onDropOn = (e: DragEvent<HTMLElement>, target: string | null): void => {
    e.preventDefault()
    setDrop(null)
    const paperRaw = e.dataTransfer.getData(DRAG_PAPERS)
    if (paperRaw) return p.onDropPapers(target, JSON.parse(paperRaw) as string[])
    const dragId = e.dataTransfer.getData(DRAG_FOLDER)
    if (dragId && canNestFolder(p.folders.items, dragId, target)) p.onMoveFolder(dragId, target)
  }
  const acceptDrop = (e: DragEvent<HTMLElement>, key: string): void => {
    if (!hasType(e, DRAG_PAPERS) && !hasType(e, DRAG_FOLDER)) return
    e.preventDefault()
    setDrop((d) => (d && d.id === key ? d : { id: key, pos: 'inside' }))
  }

  const active = (cond: boolean): string => (cond ? 'pd-frail-item is-active' : 'pd-frail-item')
  const noFolder = p.folderId === undefined
  const sys = p.folders.counts

  return (
    <nav className="pd-frail" aria-label="文献分类">
      <div className="pd-frail-group">
        <button type="button" className={active(noFolder && p.view === 'all' && !p.activeViewId)} onClick={() => { p.onSelectView('all'); p.onSelectFolder(undefined) }}>
          <Layers size={14} />
          <span className="pd-frail-name">全部文献</span>
          <span className="pd-frail-count">{sys.all}</span>
        </button>
        <button
          type="button"
          className={active(p.folderId === 'none') + (drop?.id === 'none' ? ' is-drop' : '')}
          onClick={() => p.onSelectFolder('none')}
          onDragOver={(e) => acceptDrop(e, 'none')}
          onDragLeave={() => setDrop((d) => (d?.id === 'none' ? null : d))}
          onDrop={(e) => onDropOn(e, null)}
          title="还没有放进任何文件夹的论文；把论文拖到这里可以移出文件夹"
        >
          <Inbox size={14} />
          <span className="pd-frail-name">未分类</span>
          <span className="pd-frail-count">{sys.unfiled}</span>
        </button>
        <button type="button" className={active(p.view === 'recent' && noFolder)} onClick={() => { p.onSelectView('recent'); p.onSelectFolder(undefined) }}>
          <Clock size={14} />
          <span className="pd-frail-name">最近阅读</span>
          <span className="pd-frail-count">{sys.recent}</span>
        </button>
        <button type="button" className={active(p.view === 'favorite' && noFolder)} onClick={() => { p.onSelectView('favorite'); p.onSelectFolder(undefined) }}>
          <Star size={14} />
          <span className="pd-frail-name">收藏</span>
          <span className="pd-frail-count">{sys.favorite}</span>
        </button>
      </div>

      <div className="pd-frail-head" onDragOver={(e) => acceptDrop(e, '__root')} onDrop={(e) => onDropOn(e, null)} data-drop={drop?.id === '__root' ? 'inside' : undefined}>
        <span>文件夹</span>
        <IconButton icon={<Info size={13} />} label="文件夹和标签有什么区别" size="sm" selected={help} onClick={() => setHelp((v) => !v)} />
        <IconButton icon={<Plus size={14} />} label="新建文件夹" size="sm" onClick={() => setCreating(null)} />
      </div>
      {help && (
        <p className="pd-frail-help">
          文件夹管“放在哪里”：一篇论文只能放在一个文件夹里，像资料柜的一格。标签管“是什么”：一篇论文可以有多个标签，像贴在上面的便利贴。
        </p>
      )}

      <div className="pd-frail-tree" role="tree">
        {rows.map((f) => {
          const isOpen = expanded.has(f.id)
          const hasKids = f.children.length > 0
          const here = p.folderId === f.id
          return (
            <div key={f.id}>
              <div
                role="treeitem"
                aria-expanded={hasKids ? isOpen : undefined}
                aria-selected={here}
                className={`pd-frail-item pd-frail-folder${here ? ' is-active' : ''}`}
                style={{ paddingLeft: 8 + f.depth * 14 }}
                data-drop={drop?.id === f.id ? drop.pos : undefined}
                draggable={renaming !== f.id}
                onDragStart={(e) => {
                  e.dataTransfer.setData(DRAG_FOLDER, f.id)
                  e.dataTransfer.effectAllowed = 'move'
                }}
                onDragEnd={() => setDrop(null)}
                onDragOver={(e) => onDragOverFolder(e, f)}
                onDragLeave={() => setDrop((d) => (d?.id === f.id ? null : d))}
                onDrop={(e) => onDropFolder(e, f)}
                onClick={() => p.onSelectFolder(f.id)}
              >
                <button
                  type="button"
                  className="pd-frail-chev"
                  aria-label={isOpen ? '折叠' : '展开'}
                  style={{ visibility: hasKids ? 'visible' : 'hidden' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    toggle(f.id)
                  }}
                >
                  {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                </button>
                {isOpen && hasKids ? <FolderOpen size={14} /> : <FolderClosed size={14} />}
                {renaming === f.id ? (
                  <InlineName initial={f.name} placeholder="文件夹名称" onSubmit={(name) => p.onRename(f, name)} onCancel={() => setRenaming(null)} />
                ) : (
                  <span className="pd-frail-name" title={f.name}>
                    {f.name}
                  </span>
                )}
                <span className="pd-frail-count" title={hasKids ? `本文件夹 ${f.paper_count} 篇，含子文件夹共 ${f.total_count} 篇` : undefined}>
                  {f.total_count}
                </span>
                <span className="pd-frail-more" onClick={(e) => e.stopPropagation()}>
                  <Menu
                    align="end"
                    trigger={({ toggle: t, open }) => <IconButton icon={<MoreHorizontal size={14} />} label="文件夹操作" size="sm" selected={open} onClick={t} />}
                    items={[
                      { key: 'sub', label: '新建子文件夹', icon: <FolderPlus size={15} />, onSelect: () => { setExpanded((s) => new Set([...s, f.id])); setCreating(f.id) } },
                      { key: 'rename', label: '重命名', icon: <Pencil size={15} />, onSelect: () => setRenaming(f.id) },
                      { key: 'top', label: '移到最上层', icon: <X size={15} />, disabled: f.parent_id === null, onSelect: () => p.onMoveFolder(f.id, null) },
                      { key: 'sep', separator: true },
                      { key: 'del', label: '删除文件夹…', icon: <Trash2 size={15} />, danger: true, onSelect: () => p.onDelete(f) }
                    ]}
                  />
                </span>
              </div>
              {creating === f.id && (
                <div className="pd-frail-item pd-frail-folder" style={{ paddingLeft: 8 + (f.depth + 1) * 14 + 18 }}>
                  <FolderClosed size={14} />
                  <InlineName initial="" placeholder="子文件夹名称" onSubmit={(name) => p.onCreate(name, f.id)} onCancel={() => setCreating(undefined)} />
                </div>
              )}
            </div>
          )
        })}
        {creating === null && (
          <div className="pd-frail-item pd-frail-folder" style={{ paddingLeft: 8 + 18 }}>
            <FolderClosed size={14} />
            <InlineName initial="" placeholder="文件夹名称" onSubmit={(name) => p.onCreate(name, null)} onCancel={() => setCreating(undefined)} />
          </div>
        )}
        {tree.length === 0 && creating === undefined && (
          <button type="button" className="pd-frail-empty" onClick={() => setCreating(null)}>
            <FolderPlus size={14} /> 新建第一个文件夹，把相近的论文放在一起
          </button>
        )}
      </div>

      {p.views.length > 0 && (
        <>
          <div className="pd-frail-head">
            <span>智能视图</span>
          </div>
          <div className="pd-frail-group">
            {p.views.map((v) => (
              <div key={v.id} className={active(p.activeViewId === v.id)} role="button" tabIndex={0} onClick={() => p.onApplyView(v)} onKeyDown={(e) => e.key === 'Enter' && p.onApplyView(v)}>
                <Filter size={14} />
                <span className="pd-frail-name" title={v.name}>
                  {v.name}
                </span>
                <span className="pd-frail-more" onClick={(e) => e.stopPropagation()}>
                  <IconButton icon={<X size={13} />} label="删除智能视图" size="sm" onClick={() => p.onDeleteView(v)} />
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </nav>
  )
}
