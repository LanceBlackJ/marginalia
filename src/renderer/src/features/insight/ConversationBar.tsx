import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Pencil, Plus, Trash2 } from 'lucide-react'
import { Menu, type MenuItem } from '@/components/ui'
import { useInsightStore } from './insightStore'

/** 一篇论文的多个对话：互相独立，每个对话只带自己的前文。顶上一条细栏：当前对话名（点开切换）+ 新对话。 */
export function ConversationBar() {
  const conversations = useInsightStore((s) => s.conversations)
  const activeId = useInsightStore((s) => s.activeConversation)
  const busy = useInsightStore((s) => s.pending?.status === 'streaming')
  const newConversation = useInsightStore((s) => s.newConversation)
  const switchConversation = useInsightStore((s) => s.switchConversation)
  const renameConversation = useInsightStore((s) => s.renameConversation)
  const removeConversation = useInsightStore((s) => s.removeConversation)

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const active = conversations.find((c) => c.id === activeId) ?? null
  const title = active?.title ?? '新对话'

  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  const commit = (): void => {
    const t = draft.trim()
    if (active && t && t !== active.title) void renameConversation(active.id, t)
    setEditing(false)
  }

  const items: MenuItem[] = [
    ...conversations.map((c) => ({
      key: c.id,
      label: `${c.title}${c.card_count ? ` · ${c.card_count}` : ''}`,
      icon: c.id === activeId ? <Check size={14} /> : <span style={{ width: 14 }} />,
      disabled: busy,
      onSelect: () => switchConversation(c.id)
    })),
    ...(active
      ? [
          { key: 'sep', separator: true } as MenuItem,
          { key: 'rename', label: '重命名这个对话', icon: <Pencil size={14} />, onSelect: () => (setDraft(active.title), setEditing(true)) },
          { key: 'delete', label: '删除这个对话', icon: <Trash2 size={14} />, danger: true, disabled: busy, onSelect: () => void removeConversation(active.id) }
        ]
      : [])
  ]

  return (
    <div className="pd-conv" role="group" aria-label="对话">
      {editing ? (
        <input
          ref={inputRef}
          className="pd-conv-input"
          value={draft}
          maxLength={60}
          aria-label="对话名称"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commit()
            if (e.key === 'Escape') setEditing(false)
          }}
        />
      ) : (
        <Menu
          align="start"
          width={260}
          trigger={({ toggle }) => (
            <button type="button" className="pd-conv-title" onClick={toggle} disabled={conversations.length === 0} title={conversations.length > 1 ? '切换对话' : title}>
              <span className="pd-ellipsis">{title}</span>
              {conversations.length > 0 && <ChevronDown size={13} />}
            </button>
          )}
          items={items}
        />
      )}
      <button type="button" className="pd-conv-new" onClick={() => void newConversation()} disabled={busy} title="新对话：和这篇论文重新开始聊，不带前面的内容">
        <Plus size={13} /> 新对话
      </button>
    </div>
  )
}
