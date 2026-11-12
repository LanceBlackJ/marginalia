import { useLayoutEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui'
import type { RefItem } from '@/lib/types.references'
import { placePopover, refHeadline, type Placement } from './refModel'
import { useRefStore } from './refStore'

const WIDTH = 360
const MAX_SHOWN = 4

/** 一条参考文献的“谁、哪年、哪里发的”：解析不可靠（双栏弄乱了）时不给，免得给出错的信息。 */
export function refByline(item: RefItem): string {
  if (item.messy || item.merged) return ''
  const who = item.authors.length > 2 ? `${item.authors.slice(0, 2).join('、')} 等` : item.authors.join('、')
  return [who, item.year, item.venue].filter(Boolean).join(' · ')
}

/**
 * 鼠标停在正文的引用角标上时浮出的小卡片：直接写出是哪篇文献（标题、作者、年份、来源）。
 * 每条文献各有一个“查看详情”，一步打开那一篇的详情（摘要等）；点角标本身是更完整的浮层。
 */
export function RefHoverCard() {
  const hover = useRefStore((s) => s.hover)
  const byIndex = useRefStore((s) => s.byIndex)
  const keepHover = useRefStore((s) => s.keepHover)
  const hideHover = useRefStore((s) => s.hideHover)
  const openDrawer = useRefStore((s) => s.openDrawer)
  const openPopover = useRefStore((s) => s.openPopover)
  const ref = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<Placement | null>(null)

  useLayoutEffect(() => {
    if (!hover) return setPlace(null)
    const h = ref.current?.offsetHeight ?? 120
    setPlace(placePopover(hover.rect, WIDTH, { w: window.innerWidth, h: window.innerHeight }, h))
  }, [hover, byIndex])

  if (!hover) return null
  const items = hover.refs.map((i) => byIndex.get(i)).filter((x): x is RefItem => !!x)
  if (items.length === 0) return null
  const shown = items.slice(0, MAX_SHOWN)
  return (
    <div
      ref={ref}
      className="pd-refhover"
      role="tooltip"
      style={{ left: place?.left ?? 0, top: place?.top, bottom: place?.bottom, width: WIDTH, visibility: place ? 'visible' : 'hidden' }}
      onMouseEnter={keepHover}
      onMouseLeave={hideHover}
    >
      {shown.map((it) => {
        const by = refByline(it)
        return (
          <div key={it.index} className="pd-refhover-item">
            <div className="pd-refhover-title">
              <span className="pd-refhover-n">[{it.index}]</span> {refHeadline(it)}
            </div>
            {by && <div className="pd-refhover-by">{by}</div>}
            <div className="pd-refhover-actions">
              <Button size="sm" variant="primary" onClick={() => openDrawer(it.index)} title="联网查询 OpenAlex / Crossref，只发送这条参考文献的标题、作者或 DOI" aria-label={`查看详情 ${it.index}`}>
                查看详情
              </Button>
            </div>
          </div>
        )
      })}
      {items.length > shown.length && <div className="pd-refhover-by">还有 {items.length - shown.length} 条，点角标查看全部</div>}
      <div className="pd-refhover-actions">
        <button type="button" className="pd-refhover-more" onClick={() => openPopover(hover.refs, hover.rect)}>
          更多信息
        </button>
      </div>
    </div>
  )
}
