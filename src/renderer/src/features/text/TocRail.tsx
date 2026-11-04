import { useReaderStore } from '@/features/reader/readerStore'

export interface TocItem {
  key: string
  title: string
  level: number
  page: number
}

/** 目录栏里“本文表格”清单的一项：点击跳到表题（图内数据网格跳到它所在图的图注）。 */
export interface TocTable {
  key: string
  ord: number
  label: string
  page: number
}

/** 本文结构：紧凑的章节目录，当前章节高亮，点击跳转；下面是“本文表格”清单（编号、页码，点击跳转）。 */
export function TocRail({ items, active, tables = [] }: { items: TocItem[]; active: string | null; tables?: TocTable[] }) {
  const jumpToSection = useReaderStore((s) => s.jumpToSection)
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  if (items.length < 2 && tables.length === 0) return null
  return (
    <nav className="pd-toc" aria-label="本文结构">
      {items.length >= 2 && (
        <>
          <div className="pd-toc-head">本文结构</div>
          <ul>
            {items.map((h) => (
              <li key={h.key}>
                <button type="button" className="pd-toc-item" data-level={Math.min(h.level, 3)} data-active={active === h.title} onClick={() => jumpToSection(h.page, h.title)} title={h.title}>
                  {h.title}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {tables.length > 0 && (
        <div className="pd-toc-sub pd-toc-tables">
          <div className="pd-toc-head">本文表格</div>
          <ul>
            {tables.map((t) => (
              <li key={t.key}>
                <button type="button" className="pd-toc-item" onClick={() => void jumpToOrd(t.ord)} title={`${t.label} · 第 ${t.page} 页`}>
                  <span className="pd-ellipsis">{t.label}</span>
                  <span className="pd-toc-pg">p.{t.page}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </nav>
  )
}

/** 当前章节：最后一个顶部已越过阅读线的标题（tops 按出现顺序）。 */
export function activeSection(tops: Array<{ title: string; top: number }>, line: number): string | null {
  let cur: string | null = tops.length ? tops[0].title : null
  for (const t of tops) {
    if (t.top <= line) cur = t.title
    else break
  }
  return cur
}
