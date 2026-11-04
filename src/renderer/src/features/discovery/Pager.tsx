import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { IconButton } from '@/components/ui'

export const PAGE_SIZE = 20

/** 结果列表分页：换了一批结果（新的搜索、换了类型筛选）就回到第一页。 */
export function usePaging<T>(items: T[], resetKey: unknown, size = PAGE_SIZE): { page: number; pages: number; shown: T[]; setPage: (p: number) => void } {
  const [page, setPageRaw] = useState(1)
  const pages = Math.max(1, Math.ceil(items.length / size))
  useEffect(() => setPageRaw(1), [resetKey])
  const cur = Math.min(page, pages)
  const setPage = (p: number): void => {
    setPageRaw(Math.min(Math.max(1, p), pages))
    document.querySelector('.pd-dc-results')?.scrollIntoView({ block: 'start' })
  }
  return { page: cur, pages, shown: items.slice((cur - 1) * size, cur * size), setPage }
}

export function Pager({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null
  return (
    <nav className="pd-dc-pager" aria-label="分页">
      <IconButton icon={<ChevronLeft size={15} />} label="上一页" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)} />
      <span>
        第 {page} / {pages} 页 · 共 {total} 条
      </span>
      <IconButton icon={<ChevronRight size={15} />} label="下一页" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)} />
    </nav>
  )
}
