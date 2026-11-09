import { useCallback, useEffect, useState } from 'react'
import { libraryApi } from '@/lib/api.library'
import type { FolderList, PaperFacets, SmartView } from '@/lib/types.library'
import { useJobsStore } from '@/stores/jobsStore'

const EMPTY_FOLDERS: FolderList = { items: [], counts: { all: 0, unfiled: 0, favorite: 0, recent: 0 } }

/** 文件夹树与各系统视图的论文数、智能视图。任务完成（导入新论文等）时自动刷新。 */
export function useFolders() {
  const [folders, setFolders] = useState<FolderList>(EMPTY_FOLDERS)
  const [views, setViews] = useState<SmartView[]>([])
  const [loaded, setLoaded] = useState(false)
  const finishedTick = useJobsStore((s) => s.finishedTick)
  const [key, setKey] = useState(0)

  useEffect(() => {
    let alive = true
    Promise.all([libraryApi.folders(), libraryApi.smartViews()])
      .then(([f, v]) => {
        if (!alive) return
        setFolders(f)
        setViews(v.items)
        setLoaded(true)
      })
      .catch(() => alive && setLoaded(true)) // 取不到不影响文献列表本身
    return () => {
      alive = false
    }
  }, [key, finishedTick])

  const reload = useCallback(() => setKey((k) => k + 1), [])
  return { folders, views, loaded, reload }
}

/** 筛选面板用的可选值与计数；面板打开时才加载。 */
export function useFacets(enabled: boolean, dep: unknown) {
  const [facets, setFacets] = useState<PaperFacets | null>(null)
  useEffect(() => {
    if (!enabled) return
    let alive = true
    libraryApi
      .facets()
      .then((f) => alive && setFacets(f))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [enabled, dep])
  return facets
}
