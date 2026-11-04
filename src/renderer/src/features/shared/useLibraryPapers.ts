import { useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/api'
import type { PaperSummary } from '@/lib/types'
import { useJobsStore } from '@/stores/jobsStore'

/** 文献库论文列表（最多 200 篇，按导入时间倒序），导入/任务结束后自动刷新；供选择器与标题查询共用。 */
export function useLibraryPapers() {
  const [items, setItems] = useState<PaperSummary[]>([])
  const [loading, setLoading] = useState(true)
  const tick = useJobsStore((s) => s.finishedTick)

  useEffect(() => {
    let alive = true
    api.papers
      .list({ limit: 200, sort: 'imported', order: 'desc' })
      .then((r) => {
        if (alive) {
          setItems(r.items)
          setLoading(false)
        }
      })
      .catch(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [tick])

  const byId = useMemo(() => new Map(items.map((p) => [p.id, p])), [items])
  return { items, byId, loading }
}
