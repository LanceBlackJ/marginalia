import { useState } from 'react'
import { ChevronDown, Layers, Pencil, Trash2 } from 'lucide-react'
import { Button, Dialog, Input, Menu, type MenuItem } from '@/components/ui'
import { TEMP_CHAIN, useInsightStore, visibleCards } from './insightStore'

/** 分析链切换：轻量下拉（当前链名称 + 链列表），不做树状侧栏。 */
export function ChainSwitcher() {
  const chains = useInsightStore((s) => s.chains)
  const cards = useInsightStore((s) => s.cards)
  const active = useInsightStore((s) => s.activeChain)
  const setActive = useInsightStore((s) => s.setActiveChain)
  const rename = useInsightStore((s) => s.renameChain)
  const remove = useInsightStore((s) => s.removeChain)
  const [renaming, setRenaming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [title, setTitle] = useState('')

  const tempCount = cards.filter((c) => !c.saved).length
  const activeChain = chains.find((c) => c.id === active)
  const shown = visibleCards(cards, active).length
  const label = active === null ? '全部卡片' : active === TEMP_CHAIN ? '临时卡片' : (activeChain?.title ?? '分析链')

  const items: MenuItem[] = [
    { key: 'all', label: '全部卡片', hint: String(cards.length), onSelect: () => setActive(null) },
    ...chains.map((c) => ({ key: c.id, label: c.title, hint: String(c.card_count), onSelect: () => setActive(c.id) })),
    { key: 'temp', label: '临时卡片', hint: String(tempCount), onSelect: () => setActive(TEMP_CHAIN) },
    ...(activeChain
      ? [
          { key: 'sep', separator: true },
          {
            key: 'rename',
            label: '重命名这条分析链',
            icon: <Pencil size={14} />,
            onSelect: () => {
              setTitle(activeChain.title)
              setRenaming(true)
            }
          },
          { key: 'delete', label: '删除这条分析链…', icon: <Trash2 size={14} />, danger: true, onSelect: () => setDeleting(true) }
        ]
      : [])
  ]

  return (
    <>
      <Menu
        width={260}
        trigger={({ toggle, open }) => (
          <button type="button" className="pd-chain-btn" onClick={toggle} aria-haspopup="menu" aria-expanded={open} title="切换分析链">
            <Layers size={13} />
            <span className="pd-ellipsis">{label}</span>
            <span className="pd-chain-n">· {shown}</span>
            <ChevronDown size={12} />
          </button>
        )}
        items={items}
      />

      <Dialog
        open={renaming}
        onClose={() => setRenaming(false)}
        title="重命名分析链"
        width={400}
        footer={
          <>
            <Button onClick={() => setRenaming(false)}>取消</Button>
            <Button
              variant="primary"
              disabled={!title.trim() || !activeChain}
              onClick={() => {
                if (activeChain) void rename(activeChain.id, title.trim())
                setRenaming(false)
              }}
            >
              保存
            </Button>
          </>
        }
      >
        <Input value={title} onChange={setTitle} aria-label="分析链名称" onEnter={() => title.trim() && activeChain && (void rename(activeChain.id, title.trim()), setRenaming(false))} />
      </Dialog>

      <Dialog
        open={deleting}
        onClose={() => setDeleting(false)}
        title="删除这条分析链？"
        width={420}
        footer={
          <>
            <Button onClick={() => setDeleting(false)}>取消</Button>
            <Button
              variant="danger"
              solid
              onClick={() => {
                if (activeChain) void remove(activeChain.id)
                setDeleting(false)
              }}
            >
              删除
            </Button>
          </>
        }
      >
        <p className="pd-muted">会同时删除这条链下的 {activeChain?.card_count ?? 0} 张研究卡片，无法撤销。</p>
      </Dialog>
    </>
  )
}
