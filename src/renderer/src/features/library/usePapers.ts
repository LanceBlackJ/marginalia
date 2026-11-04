import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorMessage, type PaperListQuery } from '@/lib/api'
import type { PaperSummary } from '@/lib/types'
import { useJobsStore } from '@/stores/jobsStore'

interface PapersState {
  items: PaperSummary[]
  total: number
  loading: boolean
  error: string | null
}

/** 按查询条件加载文献列表；导入/任务完成时自动刷新；存在“解析中”的论文时轮询。 */
export function usePapers(query: PaperListQuery, enabled = true) {
  const [state, setState] = useState<PapersState>({ items: [], total: 0, loading: true, error: null })
  const finishedTick = useJobsStore((s) => s.finishedTick)
  const [reloadKey, setReloadKey] = useState(0)
  const hasLoaded = useRef(false)
  const queryKey = JSON.stringify(query)

  useEffect(() => {
    if (!enabled) return
    const ctrl = new AbortController()
    if (!hasLoaded.current) setState((s) => ({ ...s, loading: true }))
    api.papers
      .list(JSON.parse(queryKey) as PaperListQuery, ctrl.signal)
      .then((r) => {
        hasLoaded.current = true
        setState({ items: r.items, total: r.total, loading: false, error: null })
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return
        setState((s) => ({ ...s, loading: false, error: errorMessage(err) }))
      })
    return () => ctrl.abort()
  }, [queryKey, enabled, finishedTick, reloadKey])

  const parsing = state.items.some((p) => p.status === 'parsing')
  useEffect(() => {
    if (!parsing) return
    const t = window.setInterval(() => setReloadKey((k) => k + 1), 2000)
    return () => window.clearInterval(t)
  }, [parsing])

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])
  const patchLocal = useCallback((id: string, patch: Partial<PaperSummary>) => {
    setState((s) => ({ ...s, items: s.items.map((p) => (p.id === id ? { ...p, ...patch } : p)) }))
  }, [])
  const removeLocal = useCallback((id: string) => {
    setState((s) => ({ ...s, items: s.items.filter((p) => p.id !== id), total: Math.max(0, s.total - 1) }))
  }, [])

  return { ...state, reload, patchLocal, removeLocal }
}
