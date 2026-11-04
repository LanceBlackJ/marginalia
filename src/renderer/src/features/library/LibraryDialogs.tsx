import { useEffect, useMemo, useState } from 'react'
import { FolderClosed, Inbox } from 'lucide-react'
import { Button, Dialog, Input } from '@/components/ui'
import type { FolderItem, FolderSuggestions } from '@/lib/types.library'
import { buildFolderTree, flattenTree } from './filterModel'

/** 选一个文件夹来放论文（或选“未分类”移出文件夹）；可以顺手新建一个。 */
export function MoveDialog({
  open,
  count,
  folders,
  currentFolderId,
  onClose,
  onMove,
  onCreate
}: {
  open: boolean
  count: number
  folders: FolderItem[]
  currentFolderId?: string | null
  onClose: () => void
  onMove: (folderId: string | null) => Promise<void>
  onCreate: (name: string, parentId: string | null) => Promise<FolderItem | null>
}) {
  const [target, setTarget] = useState<string | null | undefined>(undefined) // undefined=还没选
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const rows = useMemo(() => {
    const tree = buildFolderTree(folders)
    return flattenTree(tree, new Set(folders.map((f) => f.id)))
  }, [folders])

  useEffect(() => {
    if (open) {
      setTarget(undefined)
      setName('')
    }
  }, [open])

  const create = async (): Promise<void> => {
    const n = name.trim()
    if (!n) return
    const parent = typeof target === 'string' ? target : null
    const made = await onCreate(n, parent)
    if (made) {
      setName('')
      setTarget(made.id)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={() => !busy && onClose()}
      title={`移动 ${count} 篇论文到…`}
      width={460}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={target === undefined || (target === null ? currentFolderId === null : target === currentFolderId && count === 1)}
            onClick={() => {
              setBusy(true)
              void onMove(target ?? null).finally(() => setBusy(false))
            }}
          >
            移动
          </Button>
        </>
      }
    >
      <p className="pd-muted" style={{ marginBottom: 8 }}>
        一篇论文只能放在一个文件夹里；标签不受影响。
      </p>
      <div className="pd-mv-list" role="radiogroup" aria-label="目标文件夹">
        <label className={`pd-mv-row${target === null ? ' is-on' : ''}`}>
          <input type="radio" name="mv" checked={target === null} onChange={() => setTarget(null)} />
          <Inbox size={14} /> 未分类
        </label>
        {rows.map((f) => (
          <label key={f.id} className={`pd-mv-row${target === f.id ? ' is-on' : ''}`} style={{ paddingLeft: 10 + f.depth * 16 }}>
            <input type="radio" name="mv" checked={target === f.id} onChange={() => setTarget(f.id)} />
            <FolderClosed size={14} /> {f.name}
            {currentFolderId === f.id && <span className="pd-weak-text"> · 当前所在</span>}
          </label>
        ))}
      </div>
      <div className="pd-mv-new">
        <Input value={name} onChange={setName} onEnter={() => void create()} placeholder={typeof target === 'string' ? '在选中的文件夹里新建子文件夹' : '新建文件夹'} maxLength={60} aria-label="新文件夹名称" />
        <Button size="sm" onClick={() => void create()} disabled={!name.trim()}>
          新建
        </Button>
      </div>
    </Dialog>
  )
}

export function SaveViewDialog({ open, onClose, onSave }: { open: boolean; onClose: () => void; onSave: (name: string) => Promise<boolean> }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setName('')
  }, [open])
  const save = async (): Promise<void> => {
    if (!name.trim()) return
    setBusy(true)
    const ok = await onSave(name.trim())
    setBusy(false)
    if (ok) onClose()
  }
  return (
    <Dialog
      open={open}
      onClose={() => !busy && onClose()}
      title="保存为智能视图"
      width={420}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={() => void save()}>
            保存
          </Button>
        </>
      }
    >
      <p className="pd-muted" style={{ marginBottom: 8 }}>
        把当前的筛选条件存下来，之后在左侧一点就能回到这个结果。智能视图只保存条件，不保存论文，新导入的论文符合条件也会出现在里面。
      </p>
      <Input value={name} onChange={setName} onEnter={() => void save()} placeholder="例如：有笔记的 2020 年后论文" maxLength={60} aria-label="智能视图名称" autoFocus />
    </Dialog>
  )
}

export function DeleteFolderDialog({
  folder,
  childCount,
  onClose,
  onConfirm
}: {
  folder: (FolderItem & { total_count: number }) | null
  childCount: number
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  return (
    <Dialog
      open={!!folder}
      onClose={() => !busy && onClose()}
      title="删除这个文件夹？"
      width={440}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant="danger"
            solid
            loading={busy}
            onClick={() => {
              setBusy(true)
              void onConfirm().finally(() => setBusy(false))
            }}
          >
            删除文件夹
          </Button>
        </>
      }
    >
      <p style={{ fontWeight: 500 }}>{folder?.name}</p>
      <p className="pd-muted" style={{ marginTop: 8 }}>
        只删除文件夹本身{childCount > 0 ? `和它的 ${childCount} 个子文件夹` : ''}。里面的 {folder?.total_count ?? 0} 篇论文不会被删除，会回到“未分类”；标签、笔记和批注都不受影响。
      </p>
    </Dialog>
  )
}

const METHOD: Record<FolderSuggestions['method'], string> = {
  embedding: '语义相似度',
  keywords: '关键词重合',
  none: ''
}
const CONF: Record<string, string> = { high: '把握较大', medium: '把握一般', low: '仅供参考' }

/** 建议归类：只是建议，一键接受或忽略；从不自动移动论文。 */
export function SuggestionsBar({
  data,
  onAccept,
  onIgnore
}: {
  data: FolderSuggestions | null
  onAccept: (paperId: string, folderId: string) => void
  onIgnore: (paperId: string, folderId: string) => void
}) {
  const [open, setOpen] = useState(false)
  if (!data || data.items.length === 0) return null
  return (
    <section className="pd-sugg" aria-label="归类建议">
      <button type="button" className="pd-sugg-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="pd-sugg-title">归类建议 · {data.items.length} 篇未分类的论文可能适合放进已有文件夹</span>
        <span className="pd-weak-text">{open ? '收起' : '查看'}</span>
      </button>
      {open && (
        <>
          <p className="pd-sugg-note">依据：{METHOD[data.method]}。这只是建议，需要你确认，不会自动移动。</p>
          <ul className="pd-sugg-list">
            {data.items.map((s) => (
              <li key={`${s.paper_id}-${s.folder_id}`} className="pd-sugg-item">
                <div className="pd-sugg-main">
                  <div className="pd-sugg-paper" title={s.paper_title}>
                    {s.paper_title}
                  </div>
                  <div className="pd-sugg-to">
                    建议放进 <strong>{s.folder_path}</strong> · {CONF[s.confidence]}
                  </div>
                  <div className="pd-sugg-basis">{s.basis}</div>
                </div>
                <div className="pd-sugg-actions">
                  <Button size="sm" variant="primary" onClick={() => onAccept(s.paper_id, s.folder_id)}>
                    放进去
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => onIgnore(s.paper_id, s.folder_id)}>
                    忽略
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
