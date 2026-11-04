import { useMemo, useState, type ReactNode } from 'react'
import { Folder, FolderPlus, Inbox } from 'lucide-react'
import { Button, Dialog, Input, Menu, type MenuItem } from '@/components/ui'
import type { FolderItem } from '@/lib/types.library'
import { buildFolderTree, flattenTree, folderPath } from './filterModel'

interface Props {
  /** 这篇论文现在在哪个文件夹，null 是未分类 */
  currentFolderId: string | null
  folders: FolderItem[]
  /** 放进某个文件夹；null 表示移出文件夹（回到未分类） */
  onMove: (folderId: string | null) => void
  /** 新建文件夹并把这篇放进去；没有这个就不显示“新建” */
  onCreate?: (name: string) => Promise<void>
  trigger: (ctl: { open: boolean; toggle: () => void }) => ReactNode
  align?: 'start' | 'end'
}

/** 一步放进文件夹：点开就是文件夹清单，点哪个就放进哪个；也可以直接新建一个。比“移动到文件夹…”那个对话框简单。 */
export function FolderQuickMenu({ currentFolderId, folders, onMove, onCreate, trigger, align = 'end' }: Props) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const flat = useMemo(() => {
    const tree = buildFolderTree(folders)
    const all = new Set(folders.map((f) => f.id))
    return flattenTree(tree, all)
  }, [folders])

  const items: MenuItem[] = [
    ...flat.map((f) => ({
      key: f.id,
      label: `${'　'.repeat(f.depth)}${f.name}`,
      hint: f.id === currentFolderId ? '当前' : undefined,
      icon: <Folder size={15} />,
      disabled: f.id === currentFolderId,
      onSelect: () => onMove(f.id)
    })),
    ...(flat.length > 0 ? [{ key: 'sep', separator: true } as MenuItem] : []),
    ...(currentFolderId ? [{ key: 'unfile', label: '移出文件夹', icon: <Inbox size={15} />, onSelect: () => onMove(null) } as MenuItem] : []),
    ...(onCreate ? [{ key: 'new', label: '新建文件夹并放进去…', icon: <FolderPlus size={15} />, onSelect: () => setCreating(true) } as MenuItem] : [])
  ]

  const submit = async (): Promise<void> => {
    const n = name.trim()
    if (!n || !onCreate) return
    setBusy(true)
    try {
      await onCreate(n)
      setCreating(false)
      setName('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Menu align={align} trigger={trigger} items={items} />
      {onCreate && (
        <Dialog
          open={creating}
          onClose={() => setCreating(false)}
          title="新建文件夹"
          width={400}
          footer={
            <>
              <Button onClick={() => setCreating(false)}>取消</Button>
              <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={() => void submit()}>
                新建并放进去
              </Button>
            </>
          }
        >
          <Input value={name} onChange={setName} onEnter={() => void submit()} placeholder="例如：图神经网络" aria-label="文件夹名称" />
          {currentFolderId && <p className="pd-aux pd-muted">会新建在最上层，当前所在的是“{folderPath(folders, currentFolderId)}”。</p>}
        </Dialog>
      )}
    </>
  )
}
