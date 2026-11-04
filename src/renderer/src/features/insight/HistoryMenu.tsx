import { History } from 'lucide-react'
import { Menu, type MenuItem } from '@/components/ui'
import { formatRelativeTime, truncate } from '@/lib/format'
import { useInsightStore } from './insightStore'

const SHOWN = 40

/** 找到那张卡片、滚过去，并让它闪一下。 */
function scrollToCard(id: string): void {
  window.setTimeout(() => {
    const el = document.querySelector<HTMLElement>(`[data-card-id="${id}"]`)
    if (!el) return
    el.scrollIntoView({ block: 'start', behavior: 'smooth' })
    el.setAttribute('data-flash', '1')
    window.setTimeout(() => el.removeAttribute('data-flash'), 1400)
  }, 60)
}

/** 和 AI 的对话历史：最近的在上面，点一条就回到那次提问和回答。对话都是自动保存的，不用手动存。 */
export function HistoryMenu() {
  const cards = useInsightStore((s) => s.cards)
  const setActive = useInsightStore((s) => s.setActiveChain)

  const past = [...cards].filter((c) => c.status !== ('streaming' as string)).reverse()
  const items: MenuItem[] =
    past.length === 0
      ? [{ key: 'none', label: '还没有对话', disabled: true }]
      : past.slice(0, SHOWN).map((c) => ({
          key: c.id,
          label: truncate((c.question || c.object_label || '提问').replace(/\s+/g, ' '), 34),
          hint: formatRelativeTime(c.created_at),
          onSelect: () => {
            setActive(null) // 回到“全部”，这张卡片一定看得到
            scrollToCard(c.id)
          }
        }))

  return (
    <Menu
      width={300}
      align="end"
      trigger={({ toggle, open }) => (
        <button type="button" className="pd-chain-btn" onClick={toggle} aria-haspopup="menu" aria-expanded={open} title="和 AI 的对话历史">
          <History size={13} />
          <span>历史</span>
          {past.length > 0 && <span className="pd-chain-n">· {past.length}</span>}
        </button>
      )}
      items={items}
    />
  )
}
