import { useReaderStore } from '@/features/reader/readerStore'

/** 目录栏里“本文表格”清单的一项：点击跳到表题（图内数据网格跳到它所在图的图注）。 */
export interface TocTable {
  key: string
  ord: number
  label: string
  page: number
}

/** 阅读区右侧的“本文表格”清单（编号、页码，点击跳转）。章节目录只在左边的目录里，这里不再重复一份。 */
export function TocRail({ tables }: { tables: TocTable[] }) {
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  if (tables.length === 0) return null
  return (
    <nav className="pd-toc" aria-label="本文表格">
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
    </nav>
  )
}
